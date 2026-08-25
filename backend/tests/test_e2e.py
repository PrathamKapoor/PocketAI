"""End-to-end tests for PocketAI application flow.

These tests exercise the complete application flow using the mocked
llama-server, covering:
- Frontend loads correctly
- Chat message flow
- Document upload and RAG
- Conversation save
- Health and system endpoints
"""

from __future__ import annotations

import json
import time

import pytest


def test_frontend_loads_and_has_required_elements(client):
    """Verify the frontend HTML loads with all required elements."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]

    html = resp.text
    # Check for essential UI elements
    assert "PocketAI" in html
    assert 'id="input"' in html  # Chat input
    assert 'id="send-btn"' in html  # Send button
    assert 'id="save-chat-btn"' in html  # Save button
    assert 'id="shortcuts-btn"' in html  # Shortcuts button
    assert 'id="attach-btn"' in html  # Image attach button
    assert 'id="use-docs"' in html  # Docs checkbox
    assert 'id="style-chip"' in html  # Thinking style selector


def test_frontend_loads_static_assets(client):
    """Verify all static assets load correctly."""
    # CSS
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "css" in resp.headers["content-type"]

    # JavaScript
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_frontend_has_no_external_resources(client):
    """Verify the frontend has no external HTTP/HTTPS resources."""
    resp = client.get("/")
    for line in resp.text.splitlines():
        lowered = line.lower()
        if "src=" in lowered or "href=" in lowered:
            assert "http://" not in lowered and "https://" not in lowered, line


def test_health_endpoint_returns_status(client):
    """Verify health endpoint returns proper status."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "backend" in body
    assert "model" in body
    assert "runtime" in body


def test_system_endpoint_returns_hardware_info(client):
    """Verify system endpoint returns hardware information."""
    resp = client.get("/system")
    assert resp.status_code == 200
    body = resp.json()
    assert "ram" in body
    assert "cpu" in body
    assert "profile" in body
    assert "model_server" in body
    assert body["ram"]["total_mb"] > 0


def test_chat_flow_end_to_end(client, mock_llama):
    """Test complete chat flow: send message, get response, verify history."""
    # Send a message
    resp = client.post(
        "/chat",
        json={"message": "Explain what Python is"},
    )
    assert resp.status_code == 200
    body = resp.json()

    # Verify response
    assert body["answer"] == "Mock answer."
    assert body["conversation_id"] > 0
    assert body["mode"] in ["fast", "balanced"]  # Auto-classified based on message

    # Verify conversation was created
    conversation_id = body["conversation_id"]
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 200
    conv_data = resp.json()
    assert conv_data["conversation"]["id"] == conversation_id
    assert len(conv_data["messages"]) == 2  # user + assistant

    # Verify message content
    messages = conv_data["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Explain what Python is"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Mock answer."


def test_chat_stream_flow(client, mock_llama):
    """Test streaming chat flow with SSE."""
    resp = client.post(
        "/chat/stream",
        json={"message": "What is machine learning?"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    # Parse SSE events
    events = []
    for block in resp.text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_type = "message"
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if data:
            events.append((event_type, json.loads(data)))

    # Verify event structure
    types = [t for t, _ in events]
    assert "meta" in types
    assert "delta" in types
    assert "done" in types

    # Verify meta event
    meta = next(data for t, data in events if t == "meta")
    assert meta["conversation_id"] > 0
    assert meta["mode"] in ["fast", "balanced"]


def test_document_upload_and_rag(client, pocket_app, mock_llama):
    """Test document upload and RAG retrieval."""
    # Upload a document
    content = "# Study Notes\n\nPython is a programming language."
    files = {"file": ("notes.md", content.encode(), "text/markdown")}
    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "notes.md"
    assert body["chunk_count"] > 0

    # List documents
    resp = client.get("/documents")
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["filename"] == "notes.md"

    # Search for content
    resp = client.post(
        "/search",
        json={"query": "Python programming language", "top_k": 3},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) > 0
    assert "Python" in results[0]["text"]

    # Chat with docs enabled
    resp = client.post(
        "/chat",
        json={
            "message": "What is Python?",
            "use_documents": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Mock answer."

    # Verify RAG context was included in the system prompt
    sent_messages = mock_llama.last_payload["messages"]
    system_content = sent_messages[0]["content"]
    assert "Document search is enabled" in system_content


def test_conversation_save_flow(client, pocket_app, tmp_path):
    """Test conversation save flow."""
    import backend.main as main_module
    original_dir = main_module.SAVED_CHATS_DIR
    main_module.SAVED_CHATS_DIR = tmp_path / "saved_chats"

    try:
        # Create a conversation
        resp = client.post(
            "/chat",
            json={"message": "Tell me about computers"},
        )
        conversation_id = resp.json()["conversation_id"]

        # Save the conversation
        resp = client.post(f"/conversations/{conversation_id}/save")
        assert resp.status_code == 200
        body = resp.json()
        assert body["saved"] is True

        # Verify files exist
        from pathlib import Path
        save_dir = Path(body["directory"])
        assert save_dir.exists()
        assert (save_dir / "conversation.md").exists()
        assert (save_dir / "metadata.json").exists()

        # Verify markdown content
        md_content = (save_dir / "conversation.md").read_text(encoding="utf-8")
        assert "Tell me about computers" in md_content
        assert "## User" in md_content
        assert "## Assistant" in md_content

        # Verify metadata
        metadata = json.loads(
            (save_dir / "metadata.json").read_text(encoding="utf-8")
        )
        assert metadata["conversation_id"] == conversation_id
        assert metadata["message_count"] == 2
    finally:
        main_module.SAVED_CHATS_DIR = original_dir


def test_conversation_delete_flow(client, pocket_app):
    """Test conversation delete flow."""
    # Create a conversation
    resp = client.post(
        "/chat",
        json={"message": "What is 2+2?"},
    )
    conversation_id = resp.json()["conversation_id"]

    # Verify conversation exists
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 200

    # Delete the conversation
    resp = client.delete(f"/conversations/{conversation_id}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": conversation_id}

    # Verify conversation is gone
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 404


def test_multiple_conversations_flow(client, mock_llama):
    """Test multiple conversations flow."""
    # Create first conversation
    resp1 = client.post(
        "/chat",
        json={"message": "First question"},
    )
    conv1_id = resp1.json()["conversation_id"]

    # Create second conversation
    resp2 = client.post(
        "/chat",
        json={"message": "Second question"},
    )
    conv2_id = resp2.json()["conversation_id"]

    # Verify both conversations exist
    assert conv1_id != conv2_id

    # List conversations
    resp = client.get("/conversations")
    assert resp.status_code == 200
    convs = resp.json()
    assert len(convs) == 2

    # Open each conversation
    resp = client.get(f"/conversations/{conv1_id}")
    assert resp.status_code == 200
    assert resp.json()["conversation"]["id"] == conv1_id

    resp = client.get(f"/conversations/{conv2_id}")
    assert resp.status_code == 200
    assert resp.json()["conversation"]["id"] == conv2_id


def test_image_attachment_flow(client, pocket_app, mock_llama):
    """Test image attachment flow."""
    # Create a base64 encoded image (10x10 pixel PNG)
    # This is larger than the minimum dimension check
    import base64
    # Create a simple 10x10 PNG image
    from PIL import Image
    import io

    img = Image.new('RGB', (10, 10), color='red')
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    png_data = buffer.getvalue()
    image_b64 = base64.b64encode(png_data).decode()

    # Send message with image
    resp = client.post(
        "/chat",
        json={
            "message": "What is in this image?",
            "image": image_b64,
            "image_name": "test.png",
            "image_type": "image/png",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Mock answer."
    assert body["input_type"] == "mixed"  # Both image and text


def test_mode_selection_flow(client, mock_llama):
    """Test different thinking modes."""
    modes = ["fast", "balanced", "deep", "research", "build"]

    for mode in modes:
        resp = client.post(
            "/chat",
            json={"message": f"Test message for {mode}", "mode": mode},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == mode


def test_error_handling_flow(client, pocket_app):
    """Test error handling for invalid requests."""
    # Empty message
    resp = client.post("/chat", json={"message": ""})
    assert resp.status_code == 422

    # Non-existent conversation
    resp = client.post(
        "/chat",
        json={"message": "Continue", "conversation_id": 99999},
    )
    assert resp.status_code == 404

    # Non-existent conversation detail
    resp = client.get("/conversations/99999")
    assert resp.status_code == 404

    # Non-existent conversation delete
    resp = client.delete("/conversations/99999")
    assert resp.status_code == 404


def test_developer_mode_skills_endpoint(client, config, mock_llama):
    """Test that skills endpoint works in developer mode."""
    import httpx
    from fastapi.testclient import TestClient
    from backend.main import create_app

    config.developer_mode = True
    app = create_app(config, transport=httpx.MockTransport(mock_llama.handler))
    with TestClient(app) as dev_client:
        resp = dev_client.get("/skills")
        assert resp.status_code == 200
        skills = resp.json()
        assert len(skills) > 0
        # Verify skill structure
        for skill in skills:
            assert "id" in skill
            assert "name" in skill
            assert "description" in skill


def test_frontend_has_accessibility_attributes(client):
    """Test that frontend has basic accessibility attributes."""
    resp = client.get("/")
    html = resp.text

    # Check for ARIA attributes
    assert 'role="tablist"' in html
    assert 'role="tabpanel"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-label=' in html

    # Check for labels on form controls
    assert '<label' in html
    assert 'title=' in html


def test_static_assets_cache_headers(client):
    """Test that static assets have appropriate cache headers."""
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    # Static files should have cache headers
    assert "cache-control" in resp.headers or "etag" in resp.headers

    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "cache-control" in resp.headers or "etag" in resp.headers
