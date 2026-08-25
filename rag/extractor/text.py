"""Text extraction from user-uploaded files.

Safety: extraction only ever reads the file the user uploaded through the
API (stored under rag/uploads/<doc_id>/). Nothing in this package scans
folders, walks drives, or touches paths the user did not hand over.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".pptx"}

# Hard ceiling on extracted text so one huge document cannot blow up the
# chunker, the SQLite index, or the in-memory BM25 table.
MAX_EXTRACT_CHARS = 2_000_000

_MAX_PDF_PAGES = 1000
_MAX_PPTX_SLIDES = 500

# DrawingML <a:t> text runs live in this namespace inside .pptx slides.
_PPTX_TEXT_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
_PPTX_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


class ExtractionError(RuntimeError):
    """Raised when a file cannot be turned into text."""


def extract_text(path: Path) -> str:
    """Dispatch on extension; always returns bounded, normalized text."""
    ext = path.suffix.lower()
    if ext in {".txt", ".md", ".markdown"}:
        text = _extract_plain(path)
    elif ext == ".pdf":
        text = _extract_pdf(path)
    elif ext == ".pptx":
        text = _extract_pptx(path)
    else:
        raise ExtractionError(f"unsupported file type: {ext or path.name}")
    return _cap(text)


def _cap(text: str) -> str:
    text = text.replace("\x00", "").strip()
    if len(text) > MAX_EXTRACT_CHARS:
        text = text[:MAX_EXTRACT_CHARS]
    return text


def _extract_plain(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExtractionError(f"cannot read file: {exc}") from exc


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pypdf is a required dep, but degrade loudly
        raise ExtractionError(
            "PDF support needs the 'pypdf' package (see backend/requirements.txt)"
        ) from exc
    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # pypdf raises many parser-specific errors
        raise ExtractionError(f"not a readable PDF: {exc}") from exc

    pages: list[str] = []
    failed_pages = 0
    for page in reader.pages[:_MAX_PDF_PAGES]:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            failed_pages += 1
    text = "\n\n".join(p for p in pages if p.strip())
    if not text.strip():
        detail = f" ({failed_pages} pages failed to parse)" if failed_pages else ""
        raise ExtractionError(
            "no extractable text in this PDF — it may be scanned/image-only"
            + detail
        )
    return text


def _extract_pptx(path: Path) -> str:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ExtractionError("not a readable PPTX file") from exc

    with archive:
        slides = sorted(
            (name for name in archive.namelist() if _PPTX_SLIDE_RE.match(name)),
            key=lambda name: int(_PPTX_SLIDE_RE.match(name).group(1)),
        )[:_MAX_PPTX_SLIDES]
        if not slides:
            raise ExtractionError("no slides found in this PPTX file")

        slide_texts: list[str] = []
        for name in slides:
            try:
                root = ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError:
                continue
            runs = [el.text for el in root.iter() if el.tag == _PPTX_TEXT_TAG and el.text]
            if runs:
                slide_texts.append("\n".join(runs))

    text = "\n\n".join(slide_texts)
    if not text.strip():
        raise ExtractionError("no extractable text in this PPTX file")
    return text
