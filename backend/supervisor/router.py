"""The supervisor: thinking-style orchestration router.

Flow:

    user request
      -> resolve thinking style (auto classifies intent into one of five)
      -> clarification gate (build and deep styles only)
      -> compose the style pipeline into ONE system prompt
      -> send to llama-server (queued: single slot)
      -> validate response
      -> persist + return answer

Design decisions:
- Users pick a thinking style, not a skill. Skills are internal. Each
  style runs a pipeline of stages (skills + built-in orchestration
  stages) composed into a single model call; the mandatory Response
  Formatter stage hides all internal process so the user sees one clean
  answer, never the workflow.
- The Requirement Interrogator is a rule-based gate, not a second model
  call. On Phase 4 hardware (~8.6 tok/s) an extra LLM round-trip would
  roughly double latency for every message.
- The gate only fires in the build and deep styles. Fast, Balanced and
  Research answer short or vague messages directly so simple conversation
  is never interrupted by questionnaires.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from backend.config.loader import PocketAIConfig
from backend.schemas import ChatRequest, ChatResponse, ChatTimings
from backend.skills.loader import Skill, load_skills
from backend.storage.db import Storage
from backend.supervisor.pipelines import (
    GATED_MODES,
    build_pipeline_prompt,
    needs_clarification,
    resolve_mode,
)
from backend.image import (
    ImageInput,
    combine_user_message,
    image_system_note,
    process_image,
)
from backend.tools.hardware import inference_allowed, select_profile
from backend.tools.metrics import tracker
from backend.tools.llama_client import LlamaClient, LlamaError, LlamaResult
from backend.tools.sysinfo import get_memory

INTERROGATOR_SKILL = "requirement_interrogator"

# Tokens reserved on top of system prompt + message + generation when
# computing how much history fits in the live server context.
_CONTEXT_SAFETY_TOKENS = 256
# Per-message chat-template overhead estimate, in characters.
_MESSAGE_OVERHEAD_CHARS = 16

_RAG_CONTEXT_HEADER = (
    "\n\n---\nDocument search is enabled for this message. The excerpts below"
    " were retrieved from files the user uploaded. Ground your answer in them"
    " when they are relevant, and say so clearly when they do not contain the"
    " answer.\n\n"
)


class SupervisorError(RuntimeError):
    status_code = 500


class ConversationNotFoundError(SupervisorError):
    status_code = 404


class MemoryGuardError(SupervisorError):
    status_code = 503


class MessageTooLongError(SupervisorError):
    status_code = 422


def _trim_history(history: list[dict], char_budget: int) -> list[dict]:
    """Keep the most recent messages that fit the character budget."""
    kept: list[dict] = []
    used = 0
    for msg in reversed(history):
        cost = len(msg["content"]) + _MESSAGE_OVERHEAD_CHARS
        if used + cost > char_budget:
            break
        kept.append({"role": msg["role"], "content": msg["content"]})
        used += cost
    kept.reverse()
    return kept


class Supervisor:
    def __init__(
        self,
        cfg: PocketAIConfig,
        storage: Storage,
        client: LlamaClient,
        registry: dict[str, Skill] | None = None,
        rag=None,
    ) -> None:
        self._cfg = cfg
        self._storage = storage
        self._client = client
        self._registry = registry if registry is not None else load_skills(cfg.skills_dir)
        # Optional RagPipeline (duck-typed to keep backend importable without
        # the rag package). Used only when the request sets use_documents.
        self._rag = rag
        profile_name, profile = self._select_profile()
        self._profile_name = profile_name
        self._profile = profile
        self._server_ctx: int | None = None

    def _select_profile(self):
        return select_profile(self._cfg)

    @property
    def profile_name(self) -> str:
        return self._profile_name

    @property
    def profile(self):
        return self._profile

    @property
    def registry(self) -> dict[str, Skill]:
        return self._registry

    async def _server_context(self) -> int | None:
        """Live server context (GET /props), cached; None when unreachable."""
        if self._server_ctx is None:
            props = await self._client.props()
            n_ctx, _ = self._client.server_caps(props)
            self._server_ctx = n_ctx
        return self._server_ctx

    def _history_char_budget(self, system_prompt: str, message: str) -> int:
        est = max(1, self._cfg.chat.chars_per_token_estimate)
        ctx = self._server_ctx or self._profile.recommended_server_context
        reserved_tokens = (
            len(system_prompt) // est
            + len(message) // est
            + self._profile.max_generation_tokens
            + _CONTEXT_SAFETY_TOKENS
        )
        usable_tokens = min(
            max(0, ctx - reserved_tokens), self._profile.history_budget_tokens
        )
        return usable_tokens * est

    async def handle(self, req: ChatRequest) -> ChatResponse:
        prepared = await self.prepare(req)
        if prepared.clarification is not None:
            return ChatResponse(
                conversation_id=prepared.conversation_id,
                answer=prepared.clarification,
                clarification=True,
                mode=prepared.mode,
            )

        # Inference (queued by the client: one slot on the server).
        with tracker.track("inference", mode=prepared.mode):
            result = await self._client.chat(
                prepared.messages,
                max_tokens=self._profile.max_generation_tokens,
                enable_thinking=self._cfg.chat.enable_thinking,
            )

        # Validate response.
        warning = None
        if not result.content:
            warning = (
                "The model returned no visible content (its thinking may have"
                " consumed the token budget). Try a shorter, more specific request."
            )

        # Persist and answer. The DB `skill` column stores the resolved
        # mode id — skills are internal pipeline stages now, not user-facing.
        if prepared.persist_user:
            await asyncio.to_thread(
                self._storage.add_message,
                prepared.conversation_id,
                "user",
                prepared.message,
                None,
                prepared.mode,
                prepared.attachment_json,
            )
        await asyncio.to_thread(
            self._storage.add_message,
            prepared.conversation_id,
            "assistant",
            result.content,
            result.reasoning_content or None,
            prepared.mode,
        )
        return ChatResponse(
            conversation_id=prepared.conversation_id,
            answer=result.content,
            reasoning=result.reasoning_content or None,
            warning=warning,
            timings=_timings(result),
            mode=prepared.mode,
            workflow=prepared.workflow,
            attachment=prepared.attachment,
            input_type=prepared.input_type,
        )

    async def prepare(self, req: ChatRequest) -> "_PreparedRequest":
        """Validate and compose everything inference needs (no model call).

        Raises SupervisorError subclasses on bad input so HTTP handlers can
        return clean JSON errors before any streaming starts.
        """
        message = req.message.strip()
        if len(message) > self._cfg.chat.max_message_chars:
            raise MessageTooLongError(
                f"message exceeds {self._cfg.chat.max_message_chars} characters"
            )

        # 1. Resolve the thinking style (auto classifies the intent).
        mode = resolve_mode(req.mode, message)

        # 1b. Image is an input modality, not a document. OCR it (offline) so
        # the rest of the pipeline reasons about text. This runs before the
        # clarification gate: an attached image makes the request substantive.
        image_input: ImageInput | None = None
        if req.image is not None:
            with tracker.track("ocr_processing"):
                image_input = await asyncio.to_thread(
                    process_image,
                    req.image,
                    req.image_name,
                    req.image_type,
                    self._cfg,
                )

        # 2. Clarification gate (rule-based, no model call) — build and
        # deep only. Fast, Balanced and Research answer short messages
        # directly. Skipped when an image is attached (the request is real).
        if mode in GATED_MODES and needs_clarification(message) and not req.image:
            interrogator = self._registry.get(INTERROGATOR_SKILL)
            answer = (
                interrogator.body
                if interrogator
                else "Could you describe the task, the context, and what a good answer looks like?"
            )
            return _PreparedRequest(
                message=message,
                mode=mode,
                conversation_id=req.conversation_id or 0,
                clarification=answer,
            )

        # 3. Compose the system prompt: the whole style pipeline runs as
        # ONE model call.
        system_content, workflow = build_pipeline_prompt(
            mode, self._registry, message
        )
        if image_input is not None:
            system_content = system_content + "\n\n" + image_system_note()

        # 4. Conversation + history. Regenerating replaces the last
        # assistant reply: drop it first so it stays out of the context.
        if req.conversation_id is not None:
            if await asyncio.to_thread(
                self._storage.get_conversation, req.conversation_id
            ) is None:
                raise ConversationNotFoundError(
                    f"conversation {req.conversation_id} not found"
                )
            conversation_id = req.conversation_id
        else:
            conversation_id = await asyncio.to_thread(
                self._storage.create_conversation, message[:60]
            )
        if req.regenerate and req.conversation_id is not None:
            await asyncio.to_thread(
                self._storage.delete_last_assistant_message, conversation_id
            )
        history = await asyncio.to_thread(
            self._storage.get_history,
            conversation_id,
            self._cfg.chat.max_history_messages,
        )

        # 5. Prepare prompt. Retrieved document context (when requested) is
        # appended to the system prompt so it participates in the history
        # budget.
        if req.use_documents and self._rag is not None:
            with tracker.track("rag_retrieval"):
                context = await asyncio.to_thread(self._rag.context_for, message)
            if context:
                system_content = system_content + _RAG_CONTEXT_HEADER + context
        await self._server_context()

        # 5b. The user turn the model sees is the typed question joined with
        # the OCR'd image text (if any). The combined text is also what gets
        # persisted so the conversation stays self-contained after reload.
        combined_message = combine_user_message(message, image_input)
        input_type = (
            "mixed"
            if (image_input is not None and message)
            else "image"
            if image_input is not None
            else "text"
        )
        char_budget = self._history_char_budget(system_content, message)

        # On regenerate the user message is already the last history entry:
        # keep it as the prompt's final user turn instead of duplicating it.
        persist_user = True
        regenerated_user: dict | None = None
        if req.regenerate and history and history[-1]["role"] == "user":
            regenerated_user = {
                "role": "user",
                "content": history[-1]["content"],
            }
            history = history[:-1]
            persist_user = False

        messages = [{"role": "system", "content": system_content}]
        messages.extend(_trim_history(history, char_budget))
        messages.append(
            regenerated_user or {"role": "user", "content": combined_message}
        )

        # 6. Memory guard (re-checked on every request).
        allowed, free_mb = inference_allowed(self._cfg, get_memory())
        if not allowed:
            raise MemoryGuardError(
                f"PocketAI needs more memory for this request ({free_mb} MB free)."
                " Try closing a few applications and retry."
            )

        return _PreparedRequest(
            message=combined_message,
            mode=mode,
            workflow=workflow,
            conversation_id=conversation_id,
            messages=messages,
            persist_user=persist_user,
            input_type=input_type,
            attachment=image_input.attachment if image_input else None,
        )

    async def stream_events(
        self, prepared: "_PreparedRequest"
    ) -> AsyncIterator[dict]:
        """Yield SSE-ready events for a prepared request.

        Event shapes (the endpoint serializes them as Server-Sent Events):
        - {"type": "meta",  "data": {conversation_id, mode, workflow, clarification}}
        - {"type": "delta", "data": {text}}          visible content only
        - {"type": "done",  "data": {warning, timings, ...}}
        - {"type": "error", "data": {error}}

        Thinking/reasoning tokens never appear: the client only yields
        visible content deltas.
        """
        meta = {
            "conversation_id": prepared.conversation_id,
            "mode": prepared.mode,
            "workflow": prepared.workflow,
            "clarification": prepared.clarification is not None,
            "input_type": prepared.input_type,
            "attachment": prepared.attachment,
        }
        yield {"type": "meta", "data": meta}

        if prepared.clarification is not None:
            # Rule-based gate: no model call, answer delivered in one piece.
            yield {
                "type": "delta",
                "data": {"text": prepared.clarification},
            }
            yield {"type": "done", "data": dict(meta, warning=None, timings=None)}
            return

        if prepared.persist_user:
            await asyncio.to_thread(
                self._storage.add_message,
                prepared.conversation_id,
                "user",
                prepared.message,
                None,
                prepared.mode,
                prepared.attachment_json,
            )

        parts: list[str] = []
        final: LlamaResult | None = None
        persisted = False
        try:
            async for item in self._client.chat_stream(
                prepared.messages,
                max_tokens=self._profile.max_generation_tokens,
                enable_thinking=self._cfg.chat.enable_thinking,
            ):
                if isinstance(item, LlamaResult):
                    final = item
                else:
                    parts.append(item)
                    yield {"type": "delta", "data": {"text": item}}

            content = (final.content if final else "".join(parts)).strip()
            warning = None
            if not content:
                warning = (
                    "The model returned no visible content (its thinking may"
                    " have consumed the token budget). Try a shorter, more"
                    " specific request."
                )
            if content:
                await asyncio.to_thread(
                    self._storage.add_message,
                    prepared.conversation_id,
                    "assistant",
                    content,
                    None,
                    prepared.mode,
                )
                persisted = True
            yield {
                "type": "done",
                "data": dict(
                    meta,
                    warning=warning,
                    # Plain dict so the event serializes straight to JSON.
                    timings=_timings(final).model_dump() if final else None,
                ),
            }
        except LlamaError as exc:
            yield {"type": "error", "data": {"error": str(exc)}}
        finally:
            # Client disconnect (stop button) cancels the generator mid
            # stream; keep whatever was already generated so the partial
            # answer stays in history.
            if not persisted:
                partial = "".join(parts).strip()
                if partial:
                    try:
                        await asyncio.shield(
                            asyncio.to_thread(
                                self._storage.add_message,
                                prepared.conversation_id,
                                "assistant",
                                partial,
                                None,
                                prepared.mode,
                            )
                        )
                    except asyncio.CancelledError:
                        pass


@dataclass
class _PreparedRequest:
    """Everything prepare() resolved before inference."""

    message: str
    mode: str
    conversation_id: int
    workflow: list[str] | None = None
    messages: list[dict] = field(default_factory=list)
    clarification: str | None = None
    persist_user: bool = True
    input_type: str = "text"
    attachment: dict | None = None

    @property
    def attachment_json(self) -> str | None:
        return json.dumps(self.attachment) if self.attachment else None


def _timings(result: LlamaResult) -> ChatTimings:
    return ChatTimings(
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        prompt_ms=result.timings.get("prompt_ms"),
        predicted_ms=result.timings.get("predicted_ms"),
    )
