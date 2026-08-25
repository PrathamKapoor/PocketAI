"""Image requests through the API: /chat and /chat/stream, with and without
text, plus graceful error handling. Uses the mocked llama-server so the
supervisor, OCR and streaming paths are all exercised against real OCR.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image, ImageDraw

from backend.tests.test_chat_stream import parse_sse, stream_chat


def _png(text: str = "Question: what is 7 + 5?") -> str:
    img = Image.new("RGB", (700, 200), "white")
    ImageDraw.Draw(img).text((10, 10), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_image_plus_text_streams_and_ocr_reaches_model(client, mock_llama, config):
    events = stream_chat(
        client,
        {
            "message": "Solve the question in the image.",
            "image": _png("What is 12 times 8?"),
            "image_name": "math.png",
            "image_type": "image/png",
        },
    )
    meta = events[0][1]
    assert meta["input_type"] == "mixed"
    assert meta["attachment"]["type"] == "image"
    assert meta["attachment"]["ocr_available"] is True

    # The OCR context block + typed question both reach the model.
    sent = mock_llama.last_payload["messages"]
    user_turn = sent[-1]["content"]
    assert "[Image attached" in user_turn
    assert "Solve the question" in user_turn
    # The OCR'd digits are present (Tesseract spacing may vary).
    assert "12" in user_turn and "8" in user_turn

    answer = "".join(d["text"] for t, d in events if t == "delta")
    assert answer == "Mock answer."
    assert events[-1][0] == "done"


def test_image_without_text_streams(config):
    import httpx
    from fastapi.testclient import TestClient

    from backend.main import create_app
    from backend.tests.conftest import MockLlama

    mock = MockLlama()
    app = create_app(config, transport=httpx.MockTransport(mock.handler))
    with TestClient(app) as test_client:
        resp = test_client.post(
            "/chat/stream",
            json={"image": _png("Define photosynthesis."), "image_type": "image/png"},
        )
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    meta = events[0][1]
    assert meta["input_type"] == "image"
    # The user turn is just the OCR context (no typed question).
    assert "photosynthesis" in mock.last_payload["messages"][-1]["content"]


def test_image_persisted_with_attachment_in_history(client, pocket_app):
    events = stream_chat(
        client,
        {"message": "Explain this slide.", "image": _png("Newton's laws"), "image_type": "image/png"},
    )
    cid = events[0][1]["conversation_id"]
    history = pocket_app.state.storage.get_history(cid)
    user_msg = next(m for m in history if m["role"] == "user")
    assert "[Image attached" in user_msg["content"]
    assert user_msg["attachment"] is not None
    import json

    att = json.loads(user_msg["attachment"])
    assert att["type"] == "image"


def test_non_stream_image_endpoint(client, mock_llama):
    resp = client.post(
        "/chat",
        json={"message": "describe", "image": _png("Ohm's law"), "image_type": "image/png"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["input_type"] == "mixed"
    assert body["attachment"]["type"] == "image"
    assert "Ohm" in mock_llama.last_payload["messages"][-1]["content"]


def test_image_with_documents_flag_still_works(client, mock_llama):
    resp = client.post(
        "/chat",
        json={
            "message": "compare with my notes",
            "image": _png("kinetic energy"),
            "image_type": "image/png",
            "use_documents": True,
        },
    )
    assert resp.status_code == 200
    # Both the OCR context and a RAG header reach the model together.
    content = mock_llama.last_payload["messages"][-1]["content"]
    assert "kinetic" in content.lower()


# ---- error handling -------------------------------------------------------

def test_unsupported_image_type_rejected(client):
    # Valid PNG bytes but declared as an unsupported type, and sniffed format
    # happens to be allowed; force unsupported by sending a non-image blob.
    resp = client.post(
        "/chat/stream",
        json={"message": "x", "image": base64.b64encode(b"garbage").decode()},
    )
    assert resp.status_code in (400, 415, 422)
    assert "error" in resp.json()


def test_missing_image_and_message_rejected(client):
    resp = client.post("/chat/stream", json={"message": ""})
    assert resp.status_code == 422


def test_text_only_still_works_regression(client, mock_llama):
    events = stream_chat(client, {"message": "What is 2+2?"})
    assert events[0][1]["input_type"] == "text"
    assert "".join(d["text"] for t, d in events if t == "delta") == "Mock answer."
    assert mock_llama.chat_calls == 1
