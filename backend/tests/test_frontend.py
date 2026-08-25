"""Frontend serving: the vanilla dashboard is delivered by the backend."""

from __future__ import annotations


def test_index_serves_dashboard(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "PocketAI" in resp.text
    assert "/static/app.js" in resp.text
    assert "/static/style.css" in resp.text


def test_static_app_js(client):
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert '"use strict"' in resp.text


def test_static_style_css(client):
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "css" in resp.headers["content-type"]


def test_static_missing_file_is_404(client):
    resp = client.get("/static/does-not-exist.js")
    assert resp.status_code == 404


def test_index_has_no_external_resources(client):
    """Offline guarantee: no http(s) assets, CDNs or remote fonts."""
    resp = client.get("/")
    for line in resp.text.splitlines():
        lowered = line.lower()
        if "src=" in lowered or "href=" in lowered:
            assert "http://" not in lowered and "https://" not in lowered, line
