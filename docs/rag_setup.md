# PocketAI RAG Setup (Phase 7)

Offline retrieval-augmented generation: upload documents, search them, and
optionally ground chat answers in the retrieved excerpts. Built for the 8 GB
USB target — no vector database, no cloud embeddings, no network calls. All
measurements below were taken live on the dev machine (see §9).

```
Documents → Text Extraction → Chunking → BM25 Index → Vector Search → Context → Qwen Response
```

---

## 1. Components

```
rag/
├── extractor/text.py      # PDF / TXT / MD / PPTX → plain text (bounded, per-page fault tolerant)
├── chunker/chunker.py     # paragraphs → sentences → hard slices; overlap carry
├── embeddings/base.py     # EmbeddingProvider seam (Option A, disabled by design — see §5)
├── vector_store/store.py  # SQLite single source of truth (documents + chunks)
├── vector_store/bm25.py   # In-memory BM25 lexical index (Option B, the active path)
├── retriever/retriever.py # Lazy index rebuild over the store; thread-safe
├── pipeline.py            # RagPipeline: upload → index → search → context_for
└── benchmark.py           # Indexing / retrieval / memory benchmark + sample PDF builder
```

Dependencies: `pypdf` (PDF extraction) and `python-multipart` (uploads). The
BM25 index, chunker, store, and retriever are pure Python stdlib. No vector
DB, no numpy required on the hot path.

## 2. Retrieval strategy — why BM25 (Option B)

The spec asked for the smallest reliable offline option and required the
system to keep working if embeddings are unavailable.

- **Option A — small GGUF embedding model.** Would need a *second*
  `llama-server --embeddings` process (~300–500 MB extra RAM) on a machine
  where the chat server already peaks near 4.6 GB. Qwen3.5 itself cannot
  produce embeddings. Rejected for the 8 GB target.
- **Option B — BM25 lexical retrieval.** Zero extra RAM, stdlib-only,
  deterministic, sub-millisecond search. Chosen as the active path.

`rag/embeddings/base.py` keeps the seam: an `EmbeddingProvider` protocol and
`get_embedder()` (currently `NullEmbeddings`) so a small embedder can be
swapped in later without touching the pipeline. The system works fully
without it.

## 3. Storage & safety

- **SQLite is the single source of truth** (`rag/vector_store/documents.db`,
  WAL + foreign keys). The in-memory BM25 index is rebuilt lazily from it and
  marked dirty on every add/delete.
- **Uploads live under `rag/uploads/<doc_id>/<sanitized name>`** — never
  anywhere else. `doc_id` is `uuid4().hex[:16]` and is validated against
  `^[a-f0-9]{16}$` before any filesystem operation (no path traversal).
- **Filenames are sanitized**: path components stripped, character whitelist
  (`\w . - ` and space), no leading dots, capped at 120 chars.
- **Extension allowlist** `.pdf .txt .md .markdown .pptx`; **25 MB cap**
  enforced by reading `cap + 1` bytes (bounded memory, no full-file slurp of
  an oversized body).
- **No folder or drive scanning.** Documents enter only through the upload
  endpoint. The system never indexes system directories or arbitrary paths.

Extraction bounds: PDF ≤ 1000 pages (per-page `try/except`, so one corrupt
page doesn't kill the document), PPTX ≤ 500 slides, total text capped at
2 000 000 chars, NUL bytes stripped. Scanned/image-only PDFs raise a clear
"no extractable text" error rather than indexing nothing.

## 4. Chunking

`chunker.chunk_text(text, max_chars=1200, overlap=150, max_chunks=2000)`:

- split paragraphs → sentences (`(?<=[.!?])\s+`) → hard slices, in that order
- pack pieces up to `max_chars`; on flush, carry the last `overlap` chars
  into the next chunk so boundary context isn't lost
- `overlap` is clamped to `max_chars // 4` to keep it sane
- whitespace-only input → no chunks

## 5. Endpoints

Localhost only (same loopback enforcement as the rest of the backend).

### `POST /documents/upload`

`multipart/form-data`, field `file`. Returns:

```json
{"id": "726f3e90186c4cd4", "filename": "smoke.txt", "ext": ".txt",
 "size_bytes": 59, "chunk_count": 1}
```

Errors: `415` unsupported extension, `413` too large, `400` empty /
no-extractable-text.

### `GET /documents`

```json
[{"id": "…", "filename": "smoke.txt", "ext": ".txt", "size_bytes": 59,
  "chunk_count": 1, "created_at": "2026-08-21T20:23:03+00:00"}]
```

### `DELETE /documents/{id}`

```json
{"deleted": "726f3e90186c4cd4"}
```

`404` for unknown **or malformed** ids (malformed ids never reach the
filesystem layer).

### `POST /search`

```json
{"query": "zebrafish quantum ledger", "top_k": 6}
```

→

```json
{"results": [{"doc_id": "…", "filename": "smoke.txt", "chunk_index": 0,
              "score": 0.863, "text": "The zebrafish quantum ledger …"}]}
```

Validation: `query` 1–500 chars, `top_k` 1–20 (default 6). `422` otherwise.

### `POST /chat` — `use_documents`

```json
{"message": "What is the zebrafish quantum ledger protocol?",
 "use_documents": true}
```

When `use_documents` is true, the supervisor calls
`RagPipeline.context_for(message)` (top-3 hits, ≤ 4000 chars) and appends the
excerpts to the skill's system prompt under a clear header telling the model
to ground its answer in them and to say so when they don't contain the
answer. This context participates in the normal history budget. Without the
flag, no retrieval happens.

## 6. Configuration

`config/runtime.json` → `"rag"` section (all values have safe defaults):

| Key | Default | Meaning |
|---|---|---|
| `max_upload_mb` | 25 | per-file upload cap |
| `chunk_chars` | 1200 | max chunk size |
| `chunk_overlap` | 150 | carried-overlap chars between chunks |
| `search_top_k` | 6 | default hits for `POST /search` |
| `chat_top_k` | 3 | hits injected into chat context |
| `chat_context_max_chars` | 4000 | ~1000 tokens — keeps RAG inside the SAFE history budget |
| `allowed_extensions` | `.pdf .txt .md .markdown .pptx` | upload allowlist |

Paths (`rag_uploads_dir`, `rag_database_file`) live in the `"paths"` section
and resolve relative to the PocketAI root, so the drive letter can change.

## 7. Frontend wiring

The Documents tab (`frontend/`) uploads files, lists indexed documents with a
remove button, and runs standalone searches. The chat composer has a **docs**
checkbox (`#use-docs`) that sets `use_documents: true` on the next message.
All rendering is XSS-safe (`textContent` / `createElement` only).

## 8. Supervisor integration

`backend/supervisor/router.py` accepts an optional duck-typed `rag`. In step 4
(prompt preparation), if `req.use_documents` and a retriever is present, the
system prompt becomes `skill.body + _RAG_CONTEXT_HEADER + context`. Retrieval
runs in `asyncio.to_thread` so it never blocks the event loop.

## 9. Performance (measured)

Benchmark: 20 × ~50 KB TXT documents + one 100-page generated PDF → **486
chunks**, run via `python -m rag.benchmark`.

| Metric | Result |
|---|---|
| Indexing (21 docs, 486 chunks) | **3.32 s total / ~158 ms per doc** |
| Retrieval latency (50 queries) | mean **0.76 ms**, p95 **1.00 ms**, max 1.13 ms |
| `context_for` assembly | **0.75 ms** / 3537 chars |
| Free-RAM delta after indexing | **−12 MB** (within OS noise — effectively zero overhead) |

The in-memory BM25 rebuild + search is ~1 ms at this corpus size, so retrieval
adds no perceptible latency to chat. Reproduce with:

```bat
python -m rag.benchmark --docs 20 --pdf-pages 100
```

## 10. Tests

```bat
python -m pytest backend/tests -q     :: 63 passed, 1 skipped
```

New RAG coverage: chunker bounds/overlap/hard-split/empty, BM25 tokenization +
ranking, extraction for TXT/MD/PDF/PPTX (incl. a round-tripped generated PDF
and an inline-zip PPTX), rejection of unknown extensions and garbage PDFs,
filename sanitization, pipeline persistence across restart, and the full API
cycle (upload → list → search → delete) plus every error path (415/413/400/404/422),
PDF-via-API, and chat `use_documents` context injection (asserted against the
mocked llama-server payload) and its absence when the flag is off.

## 11. Security posture

- Uploads only via the API; stored under `rag/uploads/<doc_id>/` with
  sanitized names. No folder/drive/system-directory scanning anywhere.
- `doc_id` regex-validated before any path operation; delete of a malformed
  id is a 404, never a filesystem call.
- Extension allowlist + size cap enforced at read time (bounded memory).
- SQLite is the only persistence; the BM25 index is derived, never a source
  of truth. No network egress.
