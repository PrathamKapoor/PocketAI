"""Retriever: keeps a BM25 index in sync with the SQLite chunk store.

The index is rebuilt lazily after mutations. Rebuilding is cheap (tokenize
+ count over all chunks) and keeps every read path lock-free.
"""

from __future__ import annotations

import threading

from rag.vector_store.bm25 import BM25Index
from rag.vector_store.store import DocumentStore


class Retriever:
    def __init__(self, store: DocumentStore) -> None:
        self._store = store
        self._index: BM25Index | None = None
        self._dirty = True
        self._lock = threading.Lock()

    def mark_dirty(self) -> None:
        with self._lock:
            self._dirty = True

    def _ensure_index(self) -> BM25Index:
        with self._lock:
            if self._index is None or self._dirty:
                self._index = BM25Index(self._store.load_chunks())
                self._dirty = False
            return self._index

    def search(self, query: str, top_k: int = 6) -> list[dict]:
        """Top-k chunk hits for the query; [] when the corpus is empty."""
        index = self._ensure_index()
        return index.search(query, top_k=top_k)

    def chunk_count(self) -> int:
        return len(self._ensure_index())
