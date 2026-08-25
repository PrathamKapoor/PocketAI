"""Chat endpoint tests: end-to-end model connection via the mocked server."""

from __future__ import annotations


def test_chat_end_to_end(client, mock_llama):
    resp = client.post(
        "/chat",
        json={"message": "Explain this traceback: ValueError: invalid literal"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Mock answer."
    assert body["mode"] == "build"  # auto-classified by 'traceback' signal
    assert "Debugging" in body["workflow"]
    assert body["workflow"][-1] == "Response Formatter"
    assert "skill" not in body
    assert body["conversation_id"] > 0
    assert body["clarification"] is False
    assert body["timings"]["prompt_tokens"] == 42
    assert mock_llama.chat_calls == 1
    # System prompt is the composed build pipeline (Debugging stage inside).
    sent = mock_llama.last_payload["messages"]
    assert sent[0]["role"] == "system"
    assert "debugging specialist" in sent[0]["content"]
    assert sent[-1] == {
        "role": "user",
        "content": "Explain this traceback: ValueError: invalid literal",
    }
    # Thinking is disabled by default (config/runtime.json) and the flag is
    # passed explicitly, because the model template only thinks when told to.
    assert mock_llama.last_payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_chat_persists_history(client, pocket_app):
    resp = client.post("/chat", json={"message": "Why does my code crash on startup?"})
    conversation_id = resp.json()["conversation_id"]
    history = pocket_app.state.storage.get_history(conversation_id)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["content"] == "Mock answer."
    # The legacy skill column now stores the resolved thinking style id.
    assert history[1]["skill"] == "build"


def test_chat_continues_conversation(client, mock_llama):
    first = client.post("/chat", json={"message": "Help me debug this exception please"}).json()
    second = client.post(
        "/chat",
        json={
            "message": "Here is the full stack trace you asked for",
            "conversation_id": first["conversation_id"],
        },
    ).json()
    assert second["conversation_id"] == first["conversation_id"]
    roles = [m["role"] for m in mock_llama.last_payload["messages"]]
    # system + previous user/assistant pair + current user message
    assert roles == ["system", "user", "assistant", "user"]


def test_unknown_conversation_rejected(client, mock_llama):
    resp = client.post(
        "/chat",
        json={"message": "Continue our discussion about the compiler error", "conversation_id": 99999},
    )
    assert resp.status_code == 404
    assert mock_llama.chat_calls == 0


def test_vague_message_answered_directly_in_default_fast_style(client, mock_llama):
    # Auto classifies short vague messages to fast; fast never
    # interrogates, so the message goes straight to the model.
    resp = client.post("/chat", json={"message": "help"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["clarification"] is False
    assert body["mode"] == "fast"
    assert "skill" not in body
    assert mock_llama.chat_calls == 1


def test_vague_message_clarifies_in_build_style_without_model_call(client, mock_llama):
    resp = client.post("/chat", json={"message": "help", "mode": "build"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["clarification"] is True
    assert body["mode"] == "build"
    assert "Task" in body["answer"]
    assert mock_llama.chat_calls == 0


def test_vague_message_clarifies_in_deep_style_without_model_call(client, mock_llama):
    resp = client.post("/chat", json={"message": "help", "mode": "deep"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["clarification"] is True
    assert body["mode"] == "deep"
    assert mock_llama.chat_calls == 0


def test_vague_message_answered_directly_in_research_style(client, mock_llama):
    resp = client.post("/chat", json={"message": "help", "mode": "research"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["clarification"] is False
    assert body["mode"] == "research"
    assert mock_llama.chat_calls == 1


def test_empty_message_rejected(client):
    resp = client.post("/chat", json={"message": ""})
    assert resp.status_code == 422


def test_reasoning_content_returned(config):
    import httpx
    from fastapi.testclient import TestClient

    from backend.main import create_app
    from backend.tests.conftest import MockLlama

    mock = MockLlama(answer="Final answer.", reasoning="Let me think step by step...")
    app = create_app(config, transport=httpx.MockTransport(mock.handler))
    with TestClient(app) as test_client:
        body = test_client.post(
            "/chat", json={"message": "What does this segfault mean for my program?"}
        ).json()
    assert body["answer"] == "Final answer."
    assert body["reasoning"] == "Let me think step by step..."


def test_empty_content_sets_warning(config):
    import httpx
    from fastapi.testclient import TestClient

    from backend.main import create_app
    from backend.tests.conftest import MockLlama

    mock = MockLlama(answer="", reasoning="thinking only")
    app = create_app(config, transport=httpx.MockTransport(mock.handler))
    with TestClient(app) as test_client:
        resp = test_client.post(
            "/chat", json={"message": "Explain this exception in detail please"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == ""
    assert body["warning"]
