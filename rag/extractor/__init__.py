"""Text extraction from user-uploaded files (PDF, TXT, MD, PPTX)."""

from rag.extractor.text import (
    ALLOWED_EXTENSIONS,
    ExtractionError,
    extract_text,
)

__all__ = ["ALLOWED_EXTENSIONS", "ExtractionError", "extract_text"]
