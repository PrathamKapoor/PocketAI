"""Health endpoint tests."""

from __future__ import annotations


def test_health_with_model_ready(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["backend"]["status"] == "ok"
    assert body["model"]["status"] == "ready"
    assert body["model"]["alias"] == "qwen3.5-4b"
    # Display name for the UI comes from config/model.json.
    assert body["model"]["name"] == "Qwen3.5-4B Instruct"
    assert body["runtime"]["status"] == "running"
    assert "api_key" not in resp.text


def test_health_with_model_loading(client_model_loading):
    resp = client_model_loading.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"]["status"] == "loading model"
    assert body["runtime"]["status"] == "running"


def test_health_with_runtime_stopped(client_model_down):
    resp = client_model_down.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"]["status"] == "ok"
    assert body["model"]["status"] == "unavailable"
    assert body["runtime"]["status"] == "stopped"
