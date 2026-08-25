"""System endpoint tests."""

from __future__ import annotations


def test_system_report(client):
    resp = client.get("/system")
    assert resp.status_code == 200
    body = resp.json()

    assert body["ram"]["total_mb"] > 0
    assert body["ram"]["available_mb"] > 0
    assert body["cpu"]["logical_cores"] >= 1
    assert body["cpu"]["physical_cores"] >= 1
    assert body["profile"]["name"] in {"safe", "normal", "performance"}
    assert body["profile"]["max_generation_tokens"] > 0
    # From the mocked /props:
    assert body["model_server"]["context"] == 4096
    assert body["model_server"]["parallel_slots"] == 1
    assert body["python"]
    assert "api_key" not in resp.text
