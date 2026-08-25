"""Backend startup tests."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from backend.main import create_app


def test_routes_registered(pocket_app):
    paths = {route.path for route in pocket_app.routes}
    assert {"/health", "/system", "/chat", "/skills"} <= paths


def test_app_state_ready(pocket_app):
    assert pocket_app.state.supervisor.profile_name in {
        "safe",
        "normal",
        "performance",
    }
    assert pocket_app.state.supervisor.registry  # skills loaded
    assert pocket_app.state.storage.db_path.exists()


def test_skills_endpoint_hidden_by_default(client):
    # Skills are internal architecture: normal users never see them.
    resp = client.get("/skills")
    assert resp.status_code == 404


def test_skills_endpoint_visible_in_developer_mode(config, mock_llama):
    config.developer_mode = True
    app = create_app(config, transport=httpx.MockTransport(mock_llama.handler))
    with TestClient(app) as dev_client:
        resp = dev_client.get("/skills")
    assert resp.status_code == 200
    ids = {entry["id"] for entry in resp.json()}
    assert {"debugging", "prompt_engineer", "architecture_review"} <= ids
    for entry in resp.json():
        assert entry["name"]
        assert "api_key" not in entry


def test_docs_endpoint_disabled(client):
    """Swagger UI should not be accessible."""
    resp = client.get("/docs")
    assert resp.status_code == 404


def test_redoc_endpoint_disabled(client):
    """ReDoc should not be accessible."""
    resp = client.get("/redoc")
    assert resp.status_code == 404


def test_openapi_endpoint_disabled(client):
    """OpenAPI JSON should not be accessible."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 404
