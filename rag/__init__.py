"""PocketAI RAG — lightweight offline retrieval-over-generation.

Pipeline: upload -> extract text -> chunk -> index (BM25 in SQLite) ->
retrieve -> inject into the chat prompt. No cloud, no heavy vector DB:
the whole index lives in one SQLite file plus an in-memory BM25 table
that is rebuilt lazily from it (a few MB for hundreds of documents).
"""

__version__ = "0.7.0"
