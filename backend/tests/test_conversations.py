"""Conversation history API tests (list / detail / delete)."""

from __future__ import annotations


def _chat(client, message: str) -> int:
    resp = client.post("/chat", json={"message": message})
    assert resp.status_code == 200
    return resp.json()["conversation_id"]


def test_list_conversations_empty(client):
    assert client.get("/conversations").json() == []


def test_list_conversations_after_chat(client):
    conversation_id = _chat(client, "Why does my code crash on startup?")
    listed = client.get("/conversations").json()
    assert len(listed) == 1
    assert listed[0]["id"] == conversation_id
    assert listed[0]["title"] == "Why does my code crash on startup?"
    assert listed[0]["updated_at"]


def test_conversation_detail(client, pocket_app):
    conversation_id = _chat(client, "Explain this traceback: ValueError")
    body = client.get(f"/conversations/{conversation_id}").json()
    assert body["conversation"]["id"] == conversation_id
    messages = body["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Explain this traceback: ValueError"
    assert messages[1]["content"] == "Mock answer."
    assert messages[0]["created_at"]


def test_conversation_detail_not_found(client):
    resp = client.get("/conversations/99999")
    assert resp.status_code == 404


def test_delete_conversation_cascades(client, pocket_app):
    conversation_id = _chat(client, "Help me debug this exception please")
    resp = client.delete(f"/conversations/{conversation_id}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": conversation_id}
    assert client.get("/conversations").json() == []
    assert client.get(f"/conversations/{conversation_id}").status_code == 404
    # Messages are gone too (FK cascade).
    assert pocket_app.state.storage.get_messages(conversation_id) == []


def test_delete_conversation_not_found(client):
    resp = client.delete("/conversations/99999")
    assert resp.status_code == 404


def test_save_conversation(client, pocket_app, tmp_path):
    """Save a conversation to disk."""
    # Patch the SAVED_CHATS_DIR to use temp directory
    import backend.main as main_module
    original_dir = main_module.SAVED_CHATS_DIR
    main_module.SAVED_CHATS_DIR = tmp_path / "saved_chats"

    try:
        conversation_id = _chat(client, "What is Python?")
        resp = client.post(f"/conversations/{conversation_id}/save")
        assert resp.status_code == 200
        body = resp.json()
        assert body["saved"] is True
        assert "directory" in body

        # Verify files were created
        from pathlib import Path
        save_dir = Path(body["directory"])
        assert save_dir.exists()
        assert (save_dir / "conversation.md").exists()
        assert (save_dir / "metadata.json").exists()

        # Verify markdown content
        md_content = (save_dir / "conversation.md").read_text(encoding="utf-8")
        assert "What is Python?" in md_content
        assert "## User" in md_content
        assert "## Assistant" in md_content

        # Verify metadata JSON
        import json
        metadata = json.loads((save_dir / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["conversation_id"] == conversation_id
        assert metadata["title"] == "What is Python?"
        assert metadata["message_count"] == 2
    finally:
        main_module.SAVED_CHATS_DIR = original_dir


def test_save_conversation_not_found(client, tmp_path):
    """Save a non-existent conversation returns 404."""
    import backend.main as main_module
    original_dir = main_module.SAVED_CHATS_DIR
    main_module.SAVED_CHATS_DIR = tmp_path / "saved_chats"

    try:
        resp = client.post("/conversations/99999/save")
        assert resp.status_code == 404
    finally:
        main_module.SAVED_CHATS_DIR = original_dir


def test_save_conversation_creates_unique_folders(client, pocket_app, tmp_path):
    """Multiple saves create unique folders."""
    import backend.main as main_module
    import time
    original_dir = main_module.SAVED_CHATS_DIR
    main_module.SAVED_CHATS_DIR = tmp_path / "saved_chats"

    try:
        conversation_id = _chat(client, "Tell me a joke")
        resp1 = client.post(f"/conversations/{conversation_id}/save")
        time.sleep(1.1)  # Ensure different timestamp
        resp2 = client.post(f"/conversations/{conversation_id}/save")

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        dir1 = resp1.json()["directory"]
        dir2 = resp2.json()["directory"]
        assert dir1 != dir2  # Different folders
    finally:
        main_module.SAVED_CHATS_DIR = original_dir


def test_conversation_loading_preserves_all_messages(client, pocket_app):
    """Test that loading a conversation preserves all messages."""
    # Create a conversation with multiple turns
    conversation_id = _chat(client, "First message")
    resp = client.post(
        "/chat",
        json={"message": "Second message", "conversation_id": conversation_id},
    )
    assert resp.status_code == 200
    resp = client.post(
        "/chat",
        json={"message": "Third message", "conversation_id": conversation_id},
    )
    assert resp.status_code == 200

    # Load the conversation
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 200
    body = resp.json()

    # Verify all messages are present
    messages = body["messages"]
    assert len(messages) == 6  # 3 user + 3 assistant
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "First message"
    assert messages[1]["role"] == "assistant"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "Second message"
    assert messages[3]["role"] == "assistant"
    assert messages[4]["role"] == "user"
    assert messages[4]["content"] == "Third message"
    assert messages[5]["role"] == "assistant"


def test_conversation_loading_and_continuation(client, pocket_app):
    """Test that loading a conversation allows continuation."""
    # Create a conversation
    conversation_id = _chat(client, "What is Python?")

    # Load the conversation
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 200
    body = resp.json()
    messages = body["messages"]
    assert len(messages) == 2  # 1 user + 1 assistant

    # Continue the conversation
    resp = client.post(
        "/chat",
        json={"message": "Tell me more", "conversation_id": conversation_id},
    )
    assert resp.status_code == 200

    # Verify the conversation has 4 messages now
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 200
    body = resp.json()
    messages = body["messages"]
    assert len(messages) == 4  # 2 user + 2 assistant
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "Tell me more"
