"""RAG pipeline: upload -> extract -> chunk -> index -> retrieve.

Safety model (Phase 7 spec):

- The only files this package ever reads are the ones a user uploaded
  through POST /documents/upload. They are stored under
  rag/uploads/<doc_id>/<sanitized name> and nowhere else.
- No folder scanning, no drive walking, no user-supplied paths are ever
  opened. Document ids are random hex and validated before touching disk.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from rag.chunker import chunk_text
from rag.extractor import ALLOWED_EXTENSIONS, ExtractionError, extract_text
from rag.retriever import Retriever
from rag.vector_store import DocumentStore

_DOC_ID_RE = re.compile(r"[a-f0-9]{16}")


class RagError(RuntimeError):
    status_code = 400


class UnsupportedFileType(RagError):
    status_code = 415


class FileTooLarge(RagError):
    status_code = 413


class DocumentNotFound(RagError):
    status_code = 404


def sanitize_filename(filename: str) -> str:
    """Reduce an upload name to a safe, displayable base name."""
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ]+", "_", name, flags=re.UNICODE).strip()
    name = name.lstrip(".").strip()[:120]
    return name or "document"


class RagPipeline:
    """Owns uploads on disk, chunks in SQLite, and the BM25 retriever."""

    def __init__(
        self,
        uploads_dir: Path,
        db_path: Path,
        *,
        max_upload_mb: int = 25,
        chunk_chars: int = 1200,
        chunk_overlap: int = 150,
        chat_top_k: int = 3,
        chat_context_max_chars: int = 4000,
        allowed_extensions: set[str] | None = None,
    ) -> None:
        self._uploads = Path(uploads_dir)
        self._uploads.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_upload_mb * 1024 * 1024
        self._chunk_chars = chunk_chars
        self._chunk_overlap = chunk_overlap
        self._chat_top_k = chat_top_k
        self._chat_context_max_chars = chat_context_max_chars
        self._allowed = {e.lower() for e in (allowed_extensions or ALLOWED_EXTENSIONS)}
        self.store = DocumentStore(db_path)
        self.retriever = Retriever(self.store)

    def close(self) -> None:
        self.store.close()

    # ---------------- uploads ----------------

    def index_upload(self, filename: str, data: bytes) -> dict:
        safe_name = sanitize_filename(filename)
        ext = Path(safe_name).suffix.lower()
        if ext not in self._allowed:
            raise UnsupportedFileType(
                f"unsupported file type {ext or '(none)'} — allowed:"
                f" {', '.join(sorted(self._allowed))}"
            )
        if len(data) > self._max_bytes:
            raise FileTooLarge(
                f"file is too large ({len(data)} bytes);"
                f" limit is {self._max_bytes // (1024 * 1024)} MB"
            )
        if not data:
            raise RagError("uploaded file is empty")

        doc_id = uuid.uuid4().hex[:16]
        doc_dir = self._uploads / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        path = doc_dir / safe_name
        path.write_bytes(data)

        try:
            text = extract_text(path)
        except ExtractionError as exc:
            shutil.rmtree(doc_dir, ignore_errors=True)
            raise RagError(str(exc)) from exc
        if not text.strip():
            shutil.rmtree(doc_dir, ignore_errors=True)
            raise RagError("no extractable text in this file")

        chunks = chunk_text(text, self._chunk_chars, self._chunk_overlap)
        self.store.add_document(doc_id, safe_name, ext, len(data), chunks)
        self.retriever.mark_dirty()
        return {
            "id": doc_id,
            "filename": safe_name,
            "ext": ext,
            "size_bytes": len(data),
            "chunk_count": len(chunks),
        }

    # ---------------- management ----------------

    def list_documents(self) -> list[dict]:
        return self.store.list_documents()

    def delete_document(self, doc_id: str) -> None:
        if not _DOC_ID_RE.fullmatch(doc_id or ""):
            raise DocumentNotFound(f"unknown document id: {doc_id!r}")
        if not self.store.delete_document(doc_id):
            raise DocumentNotFound(f"unknown document id: {doc_id}")
        shutil.rmtree(self._uploads / doc_id, ignore_errors=True)
        self.retriever.mark_dirty()

    # ---------------- retrieval ----------------

    def search(self, query: str, top_k: int = 6) -> list[dict]:
        return self.retriever.search(query, top_k=top_k)

    def context_for(self, query: str) -> str | None:
        """Formatted top-k excerpts for injection into the chat prompt."""
        hits = self.retriever.search(query, top_k=self._chat_top_k)
        if not hits:
            return None
        parts: list[str] = []
        budget = self._chat_context_max_chars
        for hit in hits:
            part = f"[{hit['filename']} — chunk {hit['chunk_index']}]\n{hit['text']}"
            if not parts and len(part) > budget:
                part = part[:budget]
            if len(part) > budget:
                break
            parts.append(part)
            budget -= len(part)
        return "\n\n".join(parts) if parts else None
