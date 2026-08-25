"""Async client for the llama.cpp server (OpenAI-compatible API).

The server runs with a single slot (-np 1, validated in Phase 4), so this
client serializes inference requests through a semaphore: requests queue,
they never run in parallel.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx


class LlamaError(RuntimeError):
    """The model server is unreachable or returned an error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class LlamaResult:
    content: str
    reasoning_content: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    timings: dict = field(default_factory=dict)


class LlamaClient:
    def __init__(
        self,
        host: str,
        port: int,
        api_key: str,
        alias: str,
        timeout_seconds: float = 600,
        max_parallel: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.alias = alias
        self.base_url = f"http://{host}:{port}"
        self._semaphore = asyncio.Semaphore(max(1, max_parallel))
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(float(timeout_seconds), connect=5.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def health(self, timeout: float = 2.0) -> dict | None:
        """Probe /health. None when the server is unreachable."""
        try:
            resp = await self._http.get("/health", timeout=timeout)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    async def props(self, timeout: float = 2.0) -> dict | None:
        """GET /props (live server context size etc.). None when unavailable."""
        try:
            resp = await self._http.get("/props", timeout=timeout)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    @staticmethod
    def server_caps(props: dict | None) -> tuple[int | None, int | None]:
        """Extract (n_ctx, n_parallel) from a /props payload.

        Handles both payload shapes seen across llama.cpp builds: flat
        {"n_ctx", "n_parallel"} and nested {"default_generation_settings":
        {"n_ctx"}, "total_slots"}.
        """
        if not props:
            return None, None
        nested = props.get("default_generation_settings") or {}
        n_ctx = props.get("n_ctx") or nested.get("n_ctx")
        n_parallel = props.get("n_parallel") or props.get("total_slots")
        return (
            int(n_ctx) if n_ctx else None,
            int(n_parallel) if n_parallel else None,
        )

    async def chat(
        self, messages: list[dict], max_tokens: int, enable_thinking: bool = False
    ) -> LlamaResult:
        payload = {
            "model": self.alias,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
            # The model's chat template only enables thinking when explicitly
            # true, so pass the flag in both directions.
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
        async with self._semaphore:
            try:
                resp = await self._http.post("/v1/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                raise LlamaError(f"cannot reach llama-server: {exc}") from exc

        if resp.status_code != 200:
            raise LlamaError(
                f"llama-server returned HTTP {resp.status_code}: {resp.text[:200]}",
                resp.status_code,
            )
        try:
            data = resp.json()
            message = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlamaError("unexpected response shape from llama-server") from exc

        usage = data.get("usage") or {}
        return LlamaResult(
            content=(message.get("content") or "").strip(),
            reasoning_content=(message.get("reasoning_content") or "").strip(),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            timings=data.get("timings") or {},
        )

    async def chat_stream(
        self, messages: list[dict], max_tokens: int, enable_thinking: bool = False
    ) -> AsyncIterator[str | LlamaResult]:
        """Streaming chat: yields visible content deltas, then a LlamaResult.

        Only ``delta.content`` is ever yielded. Thinking models put internal
        reasoning in ``delta.reasoning_content`` — those tokens are dropped
        here so they can never reach the UI.
        """
        payload = {
            "model": self.alias,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
        parts: list[str] = []
        usage: dict = {}
        timings: dict = {}
        async with self._semaphore:
            try:
                async with self._http.stream(
                    "POST", "/v1/chat/completions", json=payload
                ) as resp:
                    if resp.status_code != 200:
                        await resp.aread()
                        raise LlamaError(
                            "llama-server returned HTTP"
                            f" {resp.status_code}: {resp.text[:200]}",
                            resp.status_code,
                        )
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        if chunk.get("timings"):
                            timings = chunk["timings"]
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        content = delta.get("content")
                        if content:
                            parts.append(content)
                            yield content
            except httpx.HTTPError as exc:
                raise LlamaError(f"cannot reach llama-server: {exc}") from exc
        yield LlamaResult(
            content="".join(parts).strip(),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            timings=timings,
        )
