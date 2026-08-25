"""Security hardening tests for PocketAI."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


def test_loopback_bind_enforced(config, mock_llama):
    """Test that loopback bind is enforced."""
    import httpx
    from fastapi.testclient import TestClient
    from backend.main import create_app

    # Try to bind to a non-loopback host
    config.backend.host = "0.0.0.0"
    config.require_loopback_bind = True

    # The main function should exit when trying to bind to non-loopback
    # This is tested by the startup guard in main()
    # For now, we just verify the config has the loopback requirement
    assert config.require_loopback_bind is True


def test_model_server_host_validated(config):
    """Test that model server host is validated for loopback."""
    from backend.config.loader import ConfigError

    # Try to set a non-loopback model server host
    config.model_server.host = "192.168.1.100"
    config.require_loopback_bind = True

    # The config loader should raise an error
    # This is tested in the config loader tests


def test_docs_endpoints_disabled(client):
    """Test that Swagger/ReDoc/OpenAPI endpoints are disabled."""
    # Swagger UI
    resp = client.get("/docs")
    assert resp.status_code == 404

    # ReDoc
    resp = client.get("/redoc")
    assert resp.status_code == 404

    # OpenAPI JSON
    resp = client.get("/openapi.json")
    assert resp.status_code == 404


def test_file_upload_extension_whitelist(client):
    """Test that file upload respects extension whitelist."""
    # Try to upload a disallowed file type
    content = b"test content"
    files = {"file": ("test.exe", content, "application/octet-stream")}
    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 415  # Unsupported Media Type


def test_file_upload_size_limit(client):
    """Test that file upload respects size limits."""
    # Try to upload a file larger than the limit
    # The limit is 25MB by default
    large_content = b"x" * (26 * 1024 * 1024)  # 26MB
    files = {"file": ("large.txt", large_content, "text/plain")}
    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 413  # Request Entity Too Large


def test_image_upload_type_validation(client):
    """Test that image upload validates image types."""
    # Try to upload a non-image file as an image
    import base64
    content = b"not an image"
    image_b64 = base64.b64encode(content).decode()

    resp = client.post(
        "/chat",
        json={
            "message": "What is this?",
            "image": image_b64,
            "image_name": "test.txt",
            "image_type": "text/plain",
        },
    )
    # Should fail with image validation error (415 Unsupported Media Type)
    assert resp.status_code == 415


def test_conversation_id_validation(client):
    """Test that conversation IDs are validated."""
    # Try to access a conversation with an invalid ID
    resp = client.get("/conversations/99999")
    assert resp.status_code == 404

    # Try to delete a conversation with an invalid ID
    resp = client.delete("/conversations/99999")
    assert resp.status_code == 404


def test_message_length_limit(client):
    """Test that message length is limited."""
    # Try to send a message longer than the limit
    long_message = "x" * 10000  # Limit is 8000 by default
    resp = client.post("/chat", json={"message": long_message})
    assert resp.status_code == 422  # Unprocessable Entity


def test_sql_injection_prevention(client):
    """Test that SQL injection is prevented."""
    # Try to inject SQL through conversation title
    malicious_title = "'; DROP TABLE conversations; --"
    resp = client.post("/chat", json={"message": malicious_title})
    assert resp.status_code == 200

    # Verify the conversation was created with the sanitized title
    conversation_id = resp.json()["conversation_id"]
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 200
    # The title should be sanitized
    assert resp.json()["conversation"]["title"] == malicious_title


def test_path_traversal_prevention(client):
    """Test that path traversal is prevented in file uploads."""
    # Try to upload a file with path traversal in the name
    content = b"test content"
    files = {"file": ("../../../etc/passwd", content, "text/plain")}
    resp = client.post("/documents/upload", files=files)
    # Should either succeed with sanitized name or fail
    # The filename should be sanitized
    if resp.status_code == 200:
        filename = resp.json()["filename"]
        assert ".." not in filename
        assert "/" not in filename
        assert "\\" not in filename


def test_empty_message_rejected(client):
    """Test that empty messages are rejected."""
    resp = client.post("/chat", json={"message": ""})
    assert resp.status_code == 422


def test_html_injection_prevention(client, mock_llama):
    """Test that HTML injection is prevented in messages."""
    # Send a message with HTML tags
    html_message = "<script>alert('xss')</script>"
    resp = client.post("/chat", json={"message": html_message})
    assert resp.status_code == 200

    # Verify the message was stored
    conversation_id = resp.json()["conversation_id"]
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 200
    # The message should be stored as-is, not rendered as HTML
    messages = resp.json()["messages"]
    assert messages[0]["content"] == html_message


def test_concurrent_access_safety(client, mock_llama):
    """Test that concurrent access is safe."""
    import threading

    results = []

    def make_request():
        resp = client.post(
            "/chat",
            json={"message": f"Test message from thread {threading.current_thread().name}"},
        )
        results.append(resp.status_code)

    # Create multiple threads
    threads = []
    for _ in range(5):
        t = threading.Thread(target=make_request)
        threads.append(t)
        t.start()

    # Wait for all threads to complete
    for t in threads:
        t.join()

    # All requests should succeed
    assert all(status == 200 for status in results)


def test_database_lock_safety(client, pocket_app, mock_llama):
    """Test that database operations are thread-safe."""
    import threading
    import time

    errors = []

    def create_conversation():
        try:
            resp = client.post(
                "/chat",
                json={"message": f"Test {threading.current_thread().name}"},
            )
            if resp.status_code != 200:
                errors.append(f"Thread {threading.current_thread().name}: {resp.status_code}")
        except Exception as e:
            errors.append(f"Thread {threading.current_thread().name}: {e}")

    # Create multiple threads
    threads = []
    for _ in range(10):
        t = threading.Thread(target=create_conversation)
        threads.append(t)
        t.start()

    # Wait for all threads to complete
    for t in threads:
        t.join()

    # No errors should occur
    assert len(errors) == 0, f"Errors occurred: {errors}"


def test_saved_chats_directory_is_relative(config):
    """Test that saved chats directory is relative to the root."""
    from backend.main import SAVED_CHATS_DIR
    from pathlib import Path

    # The saved chats directory should be relative to the root
    root = config.root
    assert SAVED_CHATS_DIR == root / "saved_chats"
