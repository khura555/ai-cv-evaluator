import logging
import os
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
from typing import List, Any

import requests
from django.conf import settings
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

# optional Google GenAI python client
try:
    from google import genai
except Exception:
    genai = None

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", getattr(settings, "GEMINI_API_KEY", ""))
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", getattr(settings, "GEMINI_EMBED_MODEL", "text-embedding-004"))
VECTOR_SIZE = int(os.getenv("QDRANT_VECTOR_SIZE", "768"))  # adjust if you use a different embedder

if genai and GEMINI_API_KEY:
    try:
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        genai_client = None
        logger.exception("Failed to init genai client")
else:
    genai_client = None
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is not configured; embedding calls will fail.")
    else:
        logger.warning("google.genai library not available; will use REST fallback for embeddings.")


class VectorClient:
    """
    Qdrant helper for ingesting system docs and querying relevant passages.

    This implementation is defensive:
      - tries google.genai python client first (multiple call shapes)
      - falls back to REST batchEmbedContents with correct body that includes model
      - supports several QdrantClient search/openapi fallbacks
    """

    def __init__(self):
        qdrant_url = os.getenv("QDRANT_URL", getattr(settings, "QDRANT_URL", "http://localhost:6333"))
        self.client = QdrantClient(url=qdrant_url)
        self.vector_size = VECTOR_SIZE
        # REST embedding endpoint base (for fallback)
        self._gl_base = "https://generativelanguage.googleapis.com/v1beta"
        self._api_key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")

    def ingest_system_docs_if_needed(self):
        base = Path(__file__).resolve().parent.parent / "system_docs"
        collections = {
            "job_descriptions": rest.VectorParams(size=self.vector_size, distance=rest.Distance.COSINE),
            "scoring_rubrics": rest.VectorParams(size=self.vector_size, distance=rest.Distance.COSINE),
            "case_study": rest.VectorParams(size=self.vector_size, distance=rest.Distance.COSINE),
        }

        try:
            existing_collections = {c.name: c for c in self.client.get_collections().collections}
            existing = set(existing_collections.keys())
        except Exception:
            existing = set()

        for coll_name, vec_params in collections.items():
            dirpath = base / coll_name
            if not dirpath.exists():
                logger.debug("VectorClient: system_docs dir missing: %s", dirpath)
                continue

            needs_recreate = False
            if coll_name in existing:
                try:
                    info = self.client.get_collection(coll_name)
                    if hasattr(info.config, "params") and hasattr(info.config.params, "vectors"):
                        if hasattr(info.config.params.vectors, "size"):
                            current_size = info.config.params.vectors.size
                            if current_size != self.vector_size:
                                logger.warning(
                                    f"Collection {coll_name} has vector size {current_size}, expected {self.vector_size}. Recreating..."
                                )
                                needs_recreate = True
                except Exception as e:
                    logger.warning(f"Could not check collection {coll_name} config: {e}")

            if needs_recreate or coll_name not in existing:
                try:
                    if coll_name in existing:
                        self.client.delete_collection(collection_name=coll_name)
                        logger.info("Deleted collection %s to recreate", coll_name)
                    self.client.create_collection(collection_name=coll_name, vectors_config=vec_params)
                    existing.add(coll_name)
                    logger.info("Created collection %s with vector size %s", coll_name, self.vector_size)
                except Exception:
                    logger.exception("Failed to create/recreate qdrant collection %s", coll_name)

            for path in dirpath.glob("*.txt"):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf8")
                except Exception:
                    logger.exception("Failed to read file %s", path)
                    continue

                try:
                    emb = self._embed(text)
                    if not emb or len(emb) != self.vector_size or all(abs(x) < 1e-9 for x in emb):
                        logger.warning(
                            "Skipping upsert for %s: invalid embedding length=%s or zero-vector",
                            path.name,
                            len(emb) if emb else None,
                        )
                        continue

                    stable_id = uuid5(NAMESPACE_URL, f"{coll_name}:{path.name}")
                    point = rest.PointStruct(
                        id=str(stable_id),
                        vector=emb,
                        payload={"text": text, "filename": path.name, "collection": coll_name},
                    )
                    self.client.upsert(collection_name=coll_name, points=[point])
                    logger.info("Upserted %s into %s", path.name, coll_name)
                except Exception:
                    logger.exception("Failed to upsert point %s into collection %s", path, coll_name)

    def _embed(self, text: str) -> List[float]:
        """
        Obtain embedding from Gemini (GenAI). Robust: try python client, then REST fallback.
        Returns a list[float] of length self.vector_size or a zero-vector fallback.
        """
        if not text:
            return [0.0] * self.vector_size

        # Normalize model name. We will use a 'short' model id for payload like "text-embedding-004"
        model_short = EMBED_MODEL.split("/")[-1] if EMBED_MODEL.startswith("models/") else EMBED_MODEL

        # Try python genai client first (several possible call styles)
        if genai and genai_client:
            try:
                # Try a few signatures gracefully
                try:
                    # preferred modern: requests batch style
                    resp = genai_client.models.embed_content(
                        model=f"models/{model_short}",
                        requests=[{"content": {"parts": [{"text": text[:6000]}]}}],  # batch-style
                    )
                except TypeError:
                    # fallback older/simple signature
                    resp = genai_client.models.embed_content(model=f"models/{model_short}", contents=text[:6000])

                # Try to extract embeddings from known shapes
                if hasattr(resp, "responses") and resp.responses:
                    first = resp.responses[0]
                    embedding = getattr(first, "embedding", None)
                    if embedding and hasattr(embedding, "values"):
                        vec = list(embedding.values)
                        logger.debug("Got embedding (python client) len=%d", len(vec))
                        return vec
                if isinstance(resp, dict):
                    if "responses" in resp and resp["responses"]:
                        emb = resp["responses"][0].get("embedding")
                        if emb and "values" in emb:
                            return list(map(float, emb["values"]))
                    if "embedding" in resp:
                        e = resp["embedding"]
                        if isinstance(e, dict) and "values" in e:
                            return list(map(float, e["values"]))
                        if isinstance(e, list):
                            return list(map(float, e))
                embedding = getattr(resp, "embedding", None)
                if embedding and hasattr(embedding, "values"):
                    return list(embedding.values)
            except Exception:
                logger.exception("GenAI python client embedding failed; will try REST fallback")

        # REST fallback (if API key available)
        if not self._api_key:
            logger.error("No API key for generativelanguage REST calls")
            return [0.0] * self.vector_size

        try:
            # Use model path in URL but also put model in body to satisfy variants of the API
            url = f"{self._gl_base}/models/{model_short}:batchEmbedContents"
            headers = {"Content-Type": "application/json"}
            # Many users saw errors like "model is not specified" so include model key in body
            payload = {
                "model": model_short,
                "requests": [{"content": {"parts": [{"text": text[:6000]}]}}]
            }
            params = {"key": self._api_key}
            r = requests.post(url, json=payload, headers=headers, params=params, timeout=30)
            try:
                r.raise_for_status()
            except Exception:
                logger.error("Embedding HTTP request failed: %s %s", r.status_code, r.text)
                return [0.0] * self.vector_size

            data = r.json()
            # Preferred shape: {'responses': [{'embedding': {'values': [...]}}], ...}
            if "responses" in data and data["responses"]:
                emb_block = data["responses"][0].get("embedding")
                if emb_block:
                    values = emb_block.get("values") or emb_block.get("values", [])
                    if isinstance(values, list) and values:
                        logger.debug("Got embedding (REST) len=%d", len(values))
                        return list(map(float, values))
            # older variant: 'embedding' at top level
            if "embedding" in data:
                emb = data["embedding"]
                if isinstance(emb, dict) and "values" in emb:
                    return list(map(float, emb["values"]))
                if isinstance(emb, list):
                    return list(map(float, emb))
            logger.error("Embedding response had no usable embedding: %s", data)
            return [0.0] * self.vector_size
        except Exception:
            logger.exception("Embedding failed, returning zero vector")
            return [0.0] * self.vector_size

    def query_relevant(self, query_text: str, collection: str = "job_descriptions", top_k: int = 4) -> List[str]:
        try:
            q_emb = self._embed(query_text)
            hits = self._search(collection, q_emb, top_k)
            results: List[str] = []
            for h in hits:
                # qdrant search results can be objects with .payload or dicts with 'payload'
                payload = getattr(h, "payload", None)
                if payload and isinstance(payload, dict):
                    results.append(payload.get("text", ""))
                elif isinstance(h, dict) and "payload" in h:
                    results.append(h["payload"].get("text", ""))
            return results
        except Exception:
            logger.exception("Vector search failed for collection %s", collection)
            return []

    def _search(self, collection: str, vector: List[float], limit: int) -> List[Any]:
        """
        Qdrant search supporting modern .search signature or falling back to openapi_client or other internals.

        Returns list of hits in the original qdrant response form (either objects with .payload or dicts).
        """
        # 1) Modern qdrant-client search (preferred)
        try:
            if hasattr(self.client, "search"):
                logger.debug("Using QdrantClient.search()")
                return self.client.search(
                    collection_name=collection,
                    query_vector=vector,
                    limit=limit,
                    with_payload=True,
                )
        except Exception as e:
            logger.warning("QdrantClient.search() call failed: %s", e)

        # 2) openapi_client.points_api.search_points (older versions)
        try:
            oc = getattr(self.client, "openapi_client", None)
            if oc and hasattr(oc, "points_api"):
                logger.debug("Using client.openapi_client.points_api.search_points()")
                request = {"vector": vector, "limit": limit, "with_payload": True}
                response = oc.points_api.search_points(collection_name=collection, search_points=request)
                # many versions return response.result.result or response.result
                resp_val = getattr(response, "result", None)
                if resp_val is None and isinstance(response, dict):
                    resp_val = response.get("result")
                # If resp_val has 'result' key, unwrap
                if isinstance(resp_val, dict) and "result" in resp_val:
                    hits = resp_val["result"]
                else:
                    hits = resp_val or []
                return hits
        except Exception as e:
            logger.warning("openapi_client search fallback failed: %s", e)

        # 3) Try internal _client (some qdrant versions expose ._client.openapi_client or ._client.search_points)
        try:
            _client = getattr(self.client, "_client", None)
            if _client:
                # try _client.search_points if available
                if hasattr(_client, "search_points"):
                    logger.debug("Using client._client.search_points()")
                    resp = _client.search_points(collection_name=collection, vector=vector, limit=limit, with_payload=True)
                    # try to pull result
                    if isinstance(resp, dict) and "result" in resp:
                        return resp["result"]
                    return resp or []
                # try nested openapi_client
                nested = getattr(_client, "openapi_client", None)
                if nested and hasattr(nested, "points_api"):
                    logger.debug("Using client._client.openapi_client.points_api.search_points()")
                    request = {"vector": vector, "limit": limit, "with_payload": True}
                    response = nested.points_api.search_points(collection_name=collection, search_points=request)
                    resp_val = getattr(response, "result", None)
                    if isinstance(resp_val, dict) and "result" in resp_val:
                        return resp_val["result"]
                    return resp_val or []
        except Exception as e:
            logger.warning("client._client search fallbacks failed: %s", e)

        # 4) As a last resort try Qdrant's HTTP REST search endpoint directly
        try:
            # If user pointed Qdrant to a URL, we can call: POST /collections/{collection}/points/search
            base = getattr(self.client, "_base_url", None) or os.getenv("QDRANT_URL", None)
            if base:
                # ensure no trailing slash
                base = base.rstrip("/")
                url = f"{base}/collections/{collection}/points/search"
                payload = {"vector": vector, "limit": limit, "with_payload": True}
                logger.debug("Trying direct Qdrant HTTP search %s", url)
                resp = requests.post(url, json=payload, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                # result probably in data.get("result") or data.get("result", {}).get("result")
                if "result" in data and isinstance(data["result"], list):
                    return data["result"]
                if "result" in data and isinstance(data["result"], dict) and "result" in data["result"]:
                    return data["result"]["result"]
        except Exception as e:
            logger.warning("Direct HTTP Qdrant search failed: %s", e)

        raise RuntimeError("No supported search method found on QdrantClient instance.")
