"""RAG pipeline + document/search API tests."""

from __future__ import annotations

import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from rag.benchmark import make_corpus_text, make_sample_pdf
from rag.chunker import chunk_text
from rag.extractor import ExtractionError, extract_text
from rag.pipeline import RagPipeline, sanitize_filename
from rag.vector_store.bm25 import BM25Index, tokenize

UNIQUE_FACT = "The zebrafish quantum ledger protocol synchronizes nightly."


# ---------------- unit: chunker ----------------


def test_chunker_bounds_and_overlap():
    text = make_corpus_text(0, paragraphs=40)
    chunks = chunk_text(text, max_chars=800, overlap=100)
    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)
    # overlap: each chunk after the first starts with the previous chunk's
    # tail (modulo leading whitespace removed by strip)
    for prev, cur in zip(chunks, chunks[1:]):
        assert cur.startswith(prev[-100:].lstrip())


def test_chunker_hard_splits_giant_paragraph():
    text = "x" * 5000
    chunks = chunk_text(text, max_chars=1200, overlap=150)
    assert len(chunks) >= 4
    assert all(len(c) <= 1200 for c in chunks)
    assert "".join(chunks).count("x") >= 5000 - 150 * len(chunks)


def test_chunker_empty_text():
    assert chunk_text("   \n\n  ") == []


# ---------------- unit: bm25 ----------------


def test_tokenize_drops_stopwords_and_short_tokens():
    tokens = tokenize("The quick DB is a x")
    assert "the" not in tokens and "a" not in tokens and "x" not in tokens
    assert "quick" in tokens and "db" in tokens


def test_bm25_ranks_relevant_chunk_first():
    chunks = [
        ("d1", "a.txt", 0, "database indexing improves query speed dramatically"),
        ("d2", "b.txt", 0, "the weather today is sunny and warm outside"),
        ("d3", "c.txt", 0, "query optimization and database tuning guide"),
    ]
    index = BM25Index(chunks)
    hits = index.search("database query optimization", top_k=3)
    assert hits, "expected at least one hit"
    assert hits[0]["filename"] == "c.txt"
    assert all(h["score"] > 0 for h in hits)
    assert index.search("completely unrelated gibberish zzz", top_k=3) == []


# ---------------- unit: extractor ----------------


def test_extract_plain_and_markdown(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("plain notes about testing", encoding="utf-8")
    assert "plain notes" in extract_text(txt)

    md = tmp_path / "readme.md"
    md.write_text("# Title\n\nmarkdown body", encoding="utf-8")
    assert "markdown body" in extract_text(md)


def test_extract_pdf_roundtrip(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(make_sample_pdf(["Hello PocketAI world", "Second page line"]))
    text = extract_text(pdf)
    assert "Hello PocketAI world" in text
    assert "Second page line" in text


def test_extract_pptx(tmp_path):
    slide_xml = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree>"
        "<p:sp><p:txBody><a:p><a:r><a:t>Slide title text</a:t></a:r></a:p>"
        "<a:p><a:r><a:t>Bullet point text</a:t></a:r></a:p></p:txBody></p:sp>"
        "</p:spTree></p:cSld></p:sld>"
    )
    pptx = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide_xml)
    text = extract_text(pptx)
    assert "Slide title text" in text and "Bullet point text" in text


def test_extract_rejects_unknown_type(tmp_path):
    exe = tmp_path / "tool.exe"
    exe.write_bytes(b"MZ...")
    with pytest.raises(ExtractionError, match="unsupported"):
        extract_text(exe)


def test_extract_garbage_pdf_raises(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.4 but actually garbage")
    with pytest.raises(ExtractionError):
        extract_text(bad)


# ---------------- unit: pipeline ----------------


def test_sanitize_filename_strips_paths_and_junk():
    assert sanitize_filename("../../evil.txt") == "evil.txt"
    assert sanitize_filename("C:\\Users\\x\\doc.md") == "doc.md"
    # consecutive special chars collapse to a single underscore
    assert sanitize_filename("we<>ird: name.pdf") == "we_ird_ name.pdf"
    assert sanitize_filename("") == "document"


def test_pipeline_persists_across_restart(tmp_path):
    pipe = RagPipeline(tmp_path / "up", tmp_path / "docs.db")
    info = pipe.index_upload("facts.txt", UNIQUE_FACT.encode())
    pipe.close()

    reopened = RagPipeline(tmp_path / "up", tmp_path / "docs.db")
    docs = reopened.list_documents()
    assert len(docs) == 1 and docs[0]["id"] == info["id"]
    hits = reopened.search("zebrafish quantum ledger")
    assert hits and hits[0]["doc_id"] == info["id"]
    reopened.close()


# ---------------- API: documents + search ----------------


def _upload(client, name: str, data: bytes):
    return client.post(
        "/documents/upload", files={"file": (name, data, "application/octet-stream")}
    )


def test_upload_list_search_delete_cycle(client):
    resp = _upload(client, "facts.txt", UNIQUE_FACT.encode())
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert doc["filename"] == "facts.txt"
    assert doc["chunk_count"] >= 1

    listing = client.get("/documents").json()
    assert [d["id"] for d in listing] == [doc["id"]]

    hits = client.post(
        "/search", json={"query": "zebrafish quantum ledger", "top_k": 4}
    ).json()["results"]
    assert hits and hits[0]["filename"] == "facts.txt"
    assert UNIQUE_FACT.split()[1] in hits[0]["text"]

    deleted = client.delete(f"/documents/{doc['id']}")
    assert deleted.status_code == 200 and deleted.json() == {"deleted": doc["id"]}
    assert client.get("/documents").json() == []
    assert (
        client.post("/search", json={"query": "zebrafish"}).json()["results"] == []
    )


def test_upload_rejects_bad_extension(client):
    resp = _upload(client, "malware.exe", b"MZ\x90\x00")
    assert resp.status_code == 415
    assert "unsupported" in resp.json()["error"]


def test_upload_rejects_empty_file(client):
    resp = _upload(client, "empty.txt", b"")
    assert resp.status_code == 400


def test_upload_rejects_whitespace_only_text(client):
    resp = _upload(client, "blank.txt", b"   \n\n  ")
    assert resp.status_code == 400
    assert "no extractable text" in resp.json()["error"]


def test_upload_oversized_is_413(config, mock_llama):
    config.rag.max_upload_mb = 1
    app = create_app(config, transport=httpx.MockTransport(mock_llama.handler))
    with TestClient(app) as test_client:
        resp = _upload(test_client, "big.txt", b"x" * (1024 * 1024 + 2))
    assert resp.status_code == 413


def test_delete_unknown_document_is_404(client):
    assert client.delete("/documents/0123456789abcdef").status_code == 404
    # malformed ids never reach the filesystem layer
    assert client.delete("/documents/..%2F..%2Fetc").status_code == 404


def test_search_validation(client):
    assert client.post("/search", json={"query": ""}).status_code == 422
    assert client.post("/search", json={"query": "x", "top_k": 99}).status_code == 422


def test_upload_pdf_via_api(client):
    pdf_bytes = make_sample_pdf([UNIQUE_FACT, "Page two about ledgers."])
    resp = _upload(client, "manual.pdf", pdf_bytes)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ext"] == ".pdf"
    hits = client.post("/search", json={"query": "zebrafish ledger"}).json()["results"]
    assert hits and hits[0]["filename"] == "manual.pdf"


# ---------------- API: /chat integration ----------------


def test_chat_use_documents_injects_context(client, mock_llama):
    _upload(client, "facts.txt", UNIQUE_FACT.encode())
    resp = client.post(
        "/chat",
        json={"message": "What is the zebrafish quantum ledger protocol?",
              "use_documents": True},
    )
    assert resp.status_code == 200, resp.text
    system_msg = mock_llama.last_payload["messages"][0]
    assert system_msg["role"] == "system"
    assert "Document search is enabled" in system_msg["content"]
    assert "zebrafish quantum ledger" in system_msg["content"]


def test_chat_without_documents_has_no_context(client, mock_llama):
    _upload(client, "facts.txt", UNIQUE_FACT.encode())
    resp = client.post(
        "/chat", json={"message": "What is the zebrafish quantum ledger protocol?"}
    )
    assert resp.status_code == 200, resp.text
    system_msg = mock_llama.last_payload["messages"][0]
    assert "Document search is enabled" not in system_msg["content"]
