"""Okapi BM25 lexical index — the "vector store" of this project.

Why BM25 instead of embeddings (see rag/embeddings/base.py): an embedding
model would need a second llama-server process (~300-500 MB extra RAM) on
machines with 8 GB total. BM25 is pure Python, needs only token lists in
memory, and is a strong retrieval baseline for technical text.

The index is rebuilt from SQLite whenever the corpus changes; for the
target corpus size (tens to low hundreds of documents) that is well under
a second.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9']+")

# Small, boring, effective stopword list for English technical text.
_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have if in is it its of on
    or that the their then there these this to was were will with you your
    we our can could do does not no so than too very what when which who
    """.split()
)

K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    return [
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) >= 2 and tok not in _STOPWORDS
    ]


class BM25Index:
    """In-memory Okapi BM25 over a fixed list of chunks."""

    def __init__(self, chunks: list[tuple[str, str, int, str]]) -> None:
        """`chunks` items are (doc_id, filename, chunk_index, text)."""
        self._chunks = chunks
        self._tokens: list[Counter] = []
        self._lengths: list[int] = []
        df: Counter = Counter()
        for _, _, _, text in chunks:
            tokens = Counter(tokenize(text))
            self._tokens.append(tokens)
            self._lengths.append(sum(tokens.values()))
            df.update(tokens.keys())

        n = max(1, len(chunks))
        self._avgdl = (sum(self._lengths) / n) if n else 0.0
        # IDF with the standard +0.5 smoothing; floor at 0 so ubiquitous
        # terms cannot produce negative scores.
        self._idf = {
            term: max(0.0, math.log((n - freq + 0.5) / (freq + 0.5) + 1.0))
            for term, freq in df.items()
        }

    def __len__(self) -> int:
        return len(self._chunks)

    def search(self, query: str, top_k: int = 6) -> list[dict]:
        """Return top-k hits: {doc_id, filename, chunk_index, score, text}."""
        query_tokens = tokenize(query)
        if not query_tokens or not self._chunks:
            return []

        scores: list[float] = []
        for i, doc_tokens in enumerate(self._tokens):
            score = 0.0
            dl = self._lengths[i] or 1
            for term in query_tokens:
                tf = doc_tokens.get(term, 0)
                if not tf:
                    continue
                idf = self._idf.get(term, 0.0)
                denom = tf + K1 * (1 - B + B * dl / (self._avgdl or 1.0))
                score += idf * (tf * (K1 + 1)) / denom
            scores.append(score)

        ranked = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
        hits = []
        for i in ranked[:top_k]:
            if scores[i] <= 0:
                break
            doc_id, filename, chunk_index, text = self._chunks[i]
            hits.append(
                {
                    "doc_id": doc_id,
                    "filename": filename,
                    "chunk_index": chunk_index,
                    "score": round(scores[i], 4),
                    "text": text,
                }
            )
        return hits
