import json
import logging
import os
import time
from typing import Any, Dict, List

from google import genai
from django.conf import settings

logger = logging.getLogger(__name__)

API_KEY = os.getenv("GEMINI_API_KEY", getattr(settings, "GEMINI_API_KEY", ""))
MODEL_NAME = os.getenv("GEMINI_MODEL", getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash"))

if API_KEY:
    genai_client = genai.Client(api_key=API_KEY)
else:
    genai_client = None
    logger.warning("GEMINI_API_KEY is not configured; LLM calls will fail.")

class LLMClient:
    """
    Gemini helper that provides deterministic JSON responses for each stage
    of the evaluation pipeline (parsing, scoring, and synthesis).
    """

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or MODEL_NAME
        if genai_client is None:
            raise RuntimeError("Gemini client not configured (missing GEMINI_API_KEY)")
        self.client = genai_client

    def _call_json_model(
        self,
        prompt: str,
        temperature: float = 0.0,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Invoke Gemini with JSON-only response. Retries with linear backoff.
        """
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config={
                        "temperature": temperature,
                        "response_mime_type": "application/json",
                    },
                )
                text = (getattr(response, "text", "") or "").strip()
                if not text:
                    return {}
                return json.loads(text)
            except Exception as exc:
                logger.warning("Gemini call failed attempt %s: %s", attempt, exc)
                if attempt == max_retries:
                    logger.exception("Gemini max retries reached")
                    raise
                time.sleep(attempt)
        return {}

    @staticmethod
    def _join_passages(passages: List[str]) -> str:
        return "\n---\n".join(p for p in passages if p)

    def parse_resume_to_json(self, resume_text: str) -> Dict[str, Any]:
        prompt = (
            "You are a resume parser. Extract structured data and output JSON with keys:\n"
            "skills (list of strings), years_experience (number), roles (list of strings), "
            "projects (list of {name, summary}), achievements (list of strings).\n"
            "Resume:\n"
            f"{resume_text}\n"
        )
        return self._call_json_model(prompt)

    def score_cv(self, cv_struct: Dict[str, Any], jd_passages: List[str], rubric_passages: List[str]) -> Dict[str, Any]:
        prompt = (
            "You are an expert technical recruiter. Score the candidate on a 1-5 scale for: "
            "technical_skills, experience_level, achievements, cultural_fit. Provide a feedback string.\n\n"
            f"Job description excerpts:\n{self._join_passages(jd_passages)}\n\n"
            f"Scoring rubric excerpts:\n{self._join_passages(rubric_passages)}\n\n"
            f"CV structured JSON:\n{json.dumps(cv_struct)}\n"
        )
        return self._call_json_model(prompt)

    def parse_project_report_to_json(self, report_text: str) -> Dict[str, Any]:
        prompt = (
            "You are an engineering reviewer. Parse the project report and output JSON with keys: "
            "summary (string), main_components (list), used_tech (list), tests (string or list), "
            "error_handling (string), code_links (list).\n\n"
            f"Project report:\n{report_text}\n"
        )
        return self._call_json_model(prompt)

    def score_project(self, project_struct: Dict[str, Any], case_passages: List[str], rubric_passages: List[str]) -> Dict[str, Any]:
        prompt = (
            "You are a senior backend reviewer. Score the project on 1-5 scale for: "
            "correctness, code_quality, resilience, documentation, creativity. Provide feedback.\n\n"
            f"Case study brief excerpts:\n{self._join_passages(case_passages)}\n\n"
            f"Scoring rubric excerpts:\n{self._join_passages(rubric_passages)}\n\n"
            f"Project structured JSON:\n{json.dumps(project_struct)}\n"
        )
        return self._call_json_model(prompt)

    def synthesize_final(
        self,
        cv_scores: Dict[str, Any],
        project_scores: Dict[str, Any],
        cv_match_rate: float,
        project_score: float,
    ) -> Dict[str, Any]:
        prompt = (
            "You are a hiring manager. Produce JSON with keys overall_summary (3-5 sentences) "
            "and recommendation (hire|hold|reject).\n\n"
            f"CV scores: {json.dumps(cv_scores)}\n"
            f"Project scores: {json.dumps(project_scores)}\n"
            f"Numeric summary: cv_match_rate={cv_match_rate}, project_score={project_score}\n"
        )
        return self._call_json_model(prompt, temperature=0.2)
