"""Request/response schemas for the PocketAI backend API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Hard ceiling; the effective cap comes from config (chat.max_message_chars)
# and is enforced by the supervisor.
MAX_MESSAGE_CHARS_HARD = 16000

# Mirrors backend.supervisor.pipelines.MODES (kept literal here so pydantic
# can validate without importing the supervisor package).
ResponseMode = Literal[
    "auto",
    "fast",
    "balanced",
    "deep",
    "research",
    "build",
]


class ChatRequest(BaseModel):
    # A message is required UNLESS an image is attached (see validator below).
    message: str = Field(default="", max_length=MAX_MESSAGE_CHARS_HARD)
    conversation_id: int | None = None
    use_documents: bool = False
    mode: ResponseMode | None = None
    # Regenerate the last assistant reply of an existing conversation: the
    # user message is taken from history (not duplicated) and the previous
    # assistant answer is replaced.
    regenerate: bool = False
    # Optional image attachment (an input modality, not a document). The value
    # is base64-encoded image bytes with no data-URL prefix. `image_name` and
    # `image_type` are best-effort metadata from the clipboard/upload.
    image: str | None = None
    image_name: str | None = None
    image_type: str | None = None

    @model_validator(mode="after")
    def _require_message_or_image(self) -> "ChatRequest":
        if not self.message.strip() and not self.image:
            raise ValueError("provide a message or an image attachment")
        if self.image is not None and not self.image.strip():
            raise ValueError("image attachment is empty")
        return self


class ChatTimings(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_ms: float | None = None
    predicted_ms: float | None = None


class ChatResponse(BaseModel):
    conversation_id: int
    answer: str
    reasoning: str | None = None
    clarification: bool = False
    warning: str | None = None
    timings: ChatTimings | None = None
    # Resolved thinking style; workflow lists the pipeline's stage display
    # names. Both are internal metadata — the UI shows them only in
    # developer mode; the answer itself never exposes the pipeline.
    mode: str = "fast"
    workflow: list[str] | None = None
    # Present when the request included an image: minimal metadata (no bytes),
    # so the UI can render an attachment chip after reload.
    attachment: dict | None = None
    # "text", "image" or "mixed" — how the request originated.
    input_type: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=6, ge=1, le=20)


class SearchHit(BaseModel):
    doc_id: str
    filename: str
    chunk_index: int
    score: float
    text: str


class SearchResponse(BaseModel):
    results: list[SearchHit]
