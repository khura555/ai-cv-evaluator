import fitz  # PyMuPDF
import logging

logger = logging.getLogger(__name__)

def extract_text_from_pdf(path: str) -> str:
    """
    Extract text from PDF using PyMuPDF (fitz).
    Returns a string (may be empty if extraction fails).
    """
    try:
        doc = fitz.open(path)
        texts = []
        for page in doc:
            texts.append(page.get_text())
        return "\n\n".join(texts)
    except Exception as e:
        logger.exception("Failed to extract text from PDF: %s", e)
        return ""
