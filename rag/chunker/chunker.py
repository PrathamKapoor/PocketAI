"""Paragraph-aware chunking with a small overlap.

Chunks are sized for BM25 retrieval and prompt injection: big enough to
carry a thought, small enough that top-k chunks fit the token budget of an
8GB-RAM context window.
"""

from __future__ import annotations

import re

DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP = 150
# One document can contribute at most this many chunks to the index.
MAX_CHUNKS = 2000

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def chunk_text(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    max_chunks: int = MAX_CHUNKS,
) -> list[str]:
    """Split text into chunks of at most `max_chars`, with `overlap` carry-over."""
    if max_chars < 100:
        raise ValueError("max_chars must be >= 100")
    overlap = max(0, min(overlap, max_chars // 4))

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)

    pieces = _split_to_size(text, max_chars)
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if buf and len(buf) + 2 + len(piece) > max_chars:
            chunks.append(buf)
            tail = buf[-overlap:] if overlap else ""
            buf = f"{tail} {piece}".strip() if tail else piece
            if len(buf) > max_chars:  # tail did not fit with a full-size piece
                buf = piece
        else:
            buf = piece if not buf else f"{buf}\n\n{piece}"
    if buf.strip():
        chunks.append(buf.strip())

    return [c for c in chunks[:max_chunks] if c]


def _split_to_size(text: str, max_chars: int) -> list[str]:
    """Paragraphs -> sentences -> hard slices, each piece <= max_chars."""
    pieces: list[str] = []
    for paragraph in _PARAGRAPH_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        for sentence in _SENTENCE_RE.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            while len(sentence) > max_chars:
                pieces.append(sentence[:max_chars].strip())
                sentence = sentence[max_chars:]
            if sentence.strip():
                pieces.append(sentence.strip())
    return pieces
