"""Embedding providers.

Option A (small GGUF embedding model) is intentionally NOT enabled:

- It needs a second llama-server process in --embeddings mode, i.e. another
  ~300-500 MB of resident RAM and a second process lifecycle on a machine
  whose total budget is 8 GB and whose model server already peaks at 4.6 GB.
- Qwen3.5 itself is a generative model and cannot serve embeddings.

Option B (BM25 lexical retrieval, see rag/vector_store/bm25.py) is the
active path: zero extra RAM, no second model, works fully offline.

This module keeps the seam: if a small embedding GGUF ever ships with the
USB bundle, implement `EmbeddingProvider` below and swap it in via
`get_embedder()` without touching the rest of the pipeline.
"""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Interface a real embedding backend must satisfy."""

    def available(self) -> bool:
        """True when the model is loaded and embed() can be called."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; vectors must all have equal length."""


class NullEmbeddings:
    """Placeholder provider: embeddings are unavailable on this build."""

    def available(self) -> bool:
        return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError(
            "embeddings are not available in this build; BM25 retrieval is used"
        )


def get_embedder() -> EmbeddingProvider:
    """Return the active embedding provider (currently: none)."""
    return NullEmbeddings()
