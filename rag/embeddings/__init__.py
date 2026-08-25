"""Embedding seam (currently disabled — see base.py docstring)."""

from rag.embeddings.base import EmbeddingProvider, NullEmbeddings, get_embedder

__all__ = ["EmbeddingProvider", "NullEmbeddings", "get_embedder"]
