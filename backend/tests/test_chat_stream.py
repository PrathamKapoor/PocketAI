"""Streaming chat endpoint tests (SSE over the mocked llama-server)."""

from __future__ import annotations

import json


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse `event: <type>\ndata: <json>` frames into (type, data) pairs."""
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_type, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if data:
            events.append((event_type, json.loads(data)))
    return events


def stream_chat(client, payload: dict):
    resp = client.post("/chat/stream", json=payload)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    return parse_sse(resp.text)


def test_stream_end_to_end(client, mock_llama):
    events = stream_chat(
        client, {"message": "Explain this traceback: ValueError: invalid literal"}
    )
    types = [t for t, _ in events]
    assert types[0] == "meta"
    assert types[-1] == "done"
    assert "delta" in types

    meta = events[0][1]
    assert meta["conversation_id"] > 0
    assert meta["mode"] == "build"  # auto-classified by 'traceback' signal
    assert meta["clarification"] is False

    answer = "".join(data["text"] for t, data in events if t == "delta")
    assert answer == "Mock answer."

    done = events[-1][1]
    assert done["warning"] is None
    assert done["timings"]["prompt_tokens"] == 42

    # The request went to the model as a stream with thinking disabled.
    assert mock_llama.chat_calls == 1
    assert mock_llama.last_payload["stream"] is True
    assert mock_llama.last_payload["chat_template_kwargs"] == {
        "enable_thinking": False
    }


def test_stream_never_exposes_thinking_tokens(config):
    import httpx
    from fastapi.testclient import TestClient

    from backend.main import create_app
    from backend.tests.conftest import MockLlama

    mock = MockLlama(
        answer="Final answer.", reasoning="SECRET internal chain of thought"
    )
    app = create_app(config, transport=httpx.MockTransport(mock.handler))
    with TestClient(app) as test_client:
        resp = test_client.post(
            "/chat/stream",
            json={"message": "What does this segfault mean for my program?"},
        )
    assert resp.status_code == 200
    # Reasoning deltas are dropped by the client: nothing leaks to the wire.
    assert "SECRET" not in resp.text
    events = parse_sse(resp.text)
    answer = "".join(data["text"] for t, data in events if t == "delta")
    assert answer == "Final answer."


def test_stream_persists_history(client, pocket_app):
    events = stream_chat(
        client, {"message": "Why does my code crash on startup?"}
    )
    conversation_id = events[0][1]["conversation_id"]
    history = pocket_app.state.storage.get_history(conversation_id)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["content"] == "Mock answer."
    assert history[1]["skill"] == "build"


def test_stream_clarification_skips_model(client, mock_llama):
    events = stream_chat(client, {"message": "help", "mode": "build"})
    meta = events[0][1]
    assert meta["clarification"] is True
    assert meta["mode"] == "build"
    deltas = [data["text"] for t, data in events if t == "delta"]
    assert len(deltas) == 1
    assert "Task" in deltas[0]
    assert events[-1][0] == "done"
    assert mock_llama.chat_calls == 0


def test_stream_regenerate_replaces_last_answer(client, pocket_app, mock_llama):
    first = client.post(
        "/chat", json={"message": "Help me debug this exception please"}
    ).json()
    conversation_id = first["conversation_id"]

    events = stream_chat(
        client,
        {"message": "ignored on regenerate", "conversation_id": conversation_id,
         "regenerate": True},
    )
    answer = "".join(data["text"] for t, data in events if t == "delta")
    assert answer == "Mock answer."

    # No duplicated user message, old assistant reply replaced.
    history = pocket_app.state.storage.get_history(conversation_id)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "Help me debug this exception please"

    # The model saw the original user message as the final turn.
    sent = mock_llama.last_payload["messages"]
    assert sent[-1] == {
        "role": "user",
        "content": "Help me debug this exception please",
    }


def test_stream_unknown_conversation_rejected(client, mock_llama):
    resp = client.post(
        "/chat/stream",
        json={"message": "Continue our discussion", "conversation_id": 99999},
    )
    # Preparation errors surface as JSON, before any SSE starts.
    assert resp.status_code == 404
    assert mock_llama.chat_calls == 0


def test_stream_empty_message_rejected(client):
    resp = client.post("/chat/stream", json={"message": ""})
    assert resp.status_code == 422
