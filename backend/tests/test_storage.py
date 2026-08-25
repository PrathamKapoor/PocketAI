"""SQLite storage tests."""

from __future__ import annotations

import pytest

from backend.storage.db import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "test.db")


def test_conversation_roundtrip(storage):
    conv_id = storage.create_conversation("My first chat")
    conv = storage.get_conversation(conv_id)
    assert conv["title"] == "My first chat"
    assert conv["created_at"]
    assert storage.get_conversation(99999) is None


def test_list_conversations_order(storage):
    first = storage.create_conversation("older")
    second = storage.create_conversation("newer")
    storage.add_message(second, "user", "bump updated_at")
    listed = storage.list_conversations()
    assert [c["id"] for c in listed] == [second, first]


def test_history_order_and_limit(storage):
    conv_id = storage.create_conversation("history")
    for i in range(5):
        storage.add_message(conv_id, "user", f"message {i}")
    history = storage.get_history(conv_id, limit=3)
    assert [m["content"] for m in history] == ["message 2", "message 3", "message 4"]


def test_message_fields(storage):
    conv_id = storage.create_conversation("fields")
    storage.add_message(
        conv_id, "assistant", "answer", reasoning="thought", skill="debugging"
    )
    message = storage.get_history(conv_id)[0]
    assert message["role"] == "assistant"
    assert message["reasoning"] == "thought"
    assert message["skill"] == "debugging"


def test_invalid_role_rejected(storage):
    conv_id = storage.create_conversation("roles")
    with pytest.raises(Exception):
        storage.add_message(conv_id, "tool", "nope")


def test_get_messages_returns_all_oldest_first(storage):
    conv_id = storage.create_conversation("all messages")
    for i in range(4):
        storage.add_message(conv_id, "user", f"message {i}")
    messages = storage.get_messages(conv_id)
    assert [m["content"] for m in messages] == [
        "message 0", "message 1", "message 2", "message 3",
    ]
    assert all("id" in m and m["created_at"] for m in messages)


def test_delete_conversation_cascades_messages(storage):
    conv_id = storage.create_conversation("doomed")
    storage.add_message(conv_id, "user", "hello")
    assert storage.delete_conversation(conv_id) is True
    assert storage.get_conversation(conv_id) is None
    assert storage.get_messages(conv_id) == []
    assert storage.delete_conversation(conv_id) is False


def test_delete_last_assistant_message(storage):
    conv_id = storage.create_conversation("regenerate")
    storage.add_message(conv_id, "user", "question")
    storage.add_message(conv_id, "assistant", "first answer")
    storage.add_message(conv_id, "assistant", "second answer")
    assert storage.delete_last_assistant_message(conv_id) is True
    contents = [m["content"] for m in storage.get_messages(conv_id)]
    assert contents == ["question", "first answer"]
    storage.delete_last_assistant_message(conv_id)
    contents = [m["content"] for m in storage.get_messages(conv_id)]
    assert contents == ["question"]
    # No assistant message left: nothing to delete.
    assert storage.delete_last_assistant_message(conv_id) is False


def test_settings_and_metadata(storage):
    assert storage.get_setting("theme") is None
    storage.set_setting("theme", "dark")
    storage.set_setting("theme", "light")
    assert storage.get_setting("theme") == "light"
    storage.set_metadata("schema_version", "1")
    assert storage.get_metadata("schema_version") == "1"
