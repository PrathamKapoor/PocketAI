"""Shared fixtures: config on a temp database + a mocked llama-server.

Fixture names avoid 'app' on purpose: the pytest-flask plugin (if installed
on a dev machine) autouse-hijacks any fixture named 'app'.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config.loader import load_config  # noqa: E402
from backend.main import create_app  # noqa: E402


class MockLlama:
    """Simulates the llama.cpp server behind httpx.MockTransport."""

    def __init__(self, healthy: bool = True, n_ctx: int = 4096,
                 answer: str = "Mock answer.", reasoning: str = "") -> None:
        self.healthy = healthy
        self.n_ctx = n_ctx
        self.answer = answer
        self.reasoning = reasoning
        self.chat_calls = 0
        self.last_payload: dict | None = None

    def _sse_body(self) -> bytes:
        """The answer as OpenAI-style SSE chunks.

        Reasoning is emitted as reasoning_content deltas first: streaming
        clients must drop those tokens, never show them.
        """
        events: list[dict] = []
        if self.reasoning:
            events.append(
                {"choices": [{"delta": {"reasoning_content": self.reasoning}}]}
            )
        for part in re.findall(r"\S+\s*", self.answer):
            events.append({"choices": [{"delta": {"content": part}}]})
        events.append(
            {
                "choices": [{"delta": {}}],
                "usage": {"prompt_tokens": 42, "completion_tokens": 7},
                "timings": {"prompt_ms": 100.0, "predicted_ms": 800.0},
            }
        )
        lines = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
        lines += "data: [DONE]\n\n"
        return lines.encode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            if not self.healthy:
                return httpx.Response(200, json={"status": "loading model"})
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/props":
            if not self.healthy:
                return httpx.Response(503)
            return httpx.Response(
                200, json={"n_ctx": self.n_ctx, "n_parallel": 1}
            )
        if request.url.path == "/v1/chat/completions":
            self.chat_calls += 1
            self.last_payload = json.loads(request.content)
            if self.last_payload.get("stream"):
                return httpx.Response(
                    200,
                    content=self._sse_body(),
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": self.answer,
                                "reasoning_content": self.reasoning,
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 42, "completion_tokens": 7},
                    "timings": {"prompt_ms": 100.0, "predicted_ms": 800.0},
                },
            )
        return httpx.Response(404)


def _down_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


@pytest.fixture
def mock_llama() -> MockLlama:
    return MockLlama()


@pytest.fixture
def config(tmp_path):
    cfg = load_config(ROOT)
    # Absolute path wins in Path joining, so this redirects the DB to tmp.
    cfg.paths.database_file = str(tmp_path / "pocket_ai_test.db")
    cfg.paths.rag_uploads_dir = str(tmp_path / "rag_uploads")
    cfg.paths.rag_database_file = str(tmp_path / "rag_documents.db")
    return cfg


@pytest.fixture
def pocket_app(config, mock_llama):
    transport = httpx.MockTransport(mock_llama.handler)
    return create_app(config, transport=transport)


@pytest.fixture
def pocket_app_loading(config):
    loading = MockLlama(healthy=False)
    return create_app(config, transport=httpx.MockTransport(loading.handler))


@pytest.fixture
def pocket_app_down(config):
    return create_app(config, transport=httpx.MockTransport(_down_handler))


@pytest.fixture
def client(pocket_app):
    with TestClient(pocket_app) as test_client:
        yield test_client


@pytest.fixture
def client_model_loading(pocket_app_loading):
    with TestClient(pocket_app_loading) as test_client:
        yield test_client


@pytest.fixture
def client_model_down(pocket_app_down):
    with TestClient(pocket_app_down) as test_client:
        yield test_client
