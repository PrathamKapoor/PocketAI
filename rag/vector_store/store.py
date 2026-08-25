"""SQLite-backed storage for documents and their chunks.

One file (rag/vector_store/documents.db) holds all metadata and chunk text.
The BM25 index is derived from this table and rebuilt lazily, so SQLite is
the single source of truth and the USB bundle stays copy-friendly.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    ext         TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text        TEXT NOT NULL,
    PRIMARY KEY (doc_id, chunk_index)
);
"""


class DocumentStore:
    """Thread-safe (single connection + lock, same pattern as backend Storage)."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add_document(
        self,
        doc_id: str,
        filename: str,
        ext: str,
        size_bytes: int,
        chunks: list[str],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO documents (id, filename, ext, size_bytes,"
                " chunk_count, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (doc_id, filename, ext, size_bytes, len(chunks), now),
            )
            self._conn.executemany(
                "INSERT INTO chunks (doc_id, chunk_index, text) VALUES (?, ?, ?)",
                [(doc_id, i, text) for i, text in enumerate(chunks)],
            )
            self._conn.commit()

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def has_document(self, doc_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
        return row is not None

    def list_documents(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, filename, ext, size_bytes, chunk_count, created_at"
                " FROM documents ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def load_chunks(self) -> list[tuple[str, str, int, str]]:
        """All chunks as (doc_id, filename, chunk_index, text)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.doc_id, d.filename, c.chunk_index, c.text"
                " FROM chunks c JOIN documents d ON d.id = c.doc_id"
                " ORDER BY c.doc_id, c.chunk_index"
            ).fetchall()
        return [(r["doc_id"], r["filename"], r["chunk_index"], r["text"]) for r in rows]
