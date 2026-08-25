# PocketAI Architecture

> Phase 10 final document. Every claim here is verified against the running
> system or the test suite (78 passed, 1 skipped).

PocketAI is a fully portable, offline AI assistant that runs from a USB drive
on locked-down Windows PCs: no installs, no admin rights, no internet.

---

## 1. Design goals (in priority order)

1. **Works on the weakest target** — i5 11/12th-gen, 8 GB RAM, iGPU only,
   locked-down college PCs. Every design decision is judged against this box.
2. **Portable** — drive-letter agnostic, copy the folder anywhere and it runs.
3. **Offline** — zero runtime network dependencies of any kind.
4. **Safe** — loopback-only services, validated input, no code execution.
5. **Simple** — a request router, not an agent framework; stdlib where possible.

## 2. System overview

```
┌────────────────────────── host PC (nothing installed) ─────────────────────────┐
│                                                                                │
│  Browser ──► FastAPI backend (127.0.0.1:8090) ──► llama-server (127.0.0.1:8091)│
│              │  serves frontend/                  │  Qwen3.5-4B Q4_K_M (CPU)   │
│              │  supervisor (skill routing)        │  llama.cpp b10549          │
│              │  RAG (BM25 over SQLite)            │  Bearer-key protected      │
│              │  SQLite: chat history              │                            │
│              └─ bundled embeddable Python 3.13    └─ bundled DLLs, no install  │
│                                                                                │
└────────────────────────────────── all files live on the USB drive ─────────────┘
```

Two processes, both bound to 127.0.0.1:

| Process | Port | Role |
|---|---|---|
| `llama-server.exe` | 8091 | Model inference, OpenAI-compatible API, 1 slot (`-np 1`) |
| `python backend/main.py` | 8090 | UI server, supervisor, skills, RAG, chat storage |

The browser talks only to 8090. The backend is the only client of 8091.

## 3. Layers

```
launcher/   START_AI.bat → preflight.py → start model → start backend → browser
runtime/    llama.cpp build + bundled embeddable Python (build-time artifacts)
models/     Qwen3.5-4B-Q4_K_M.gguf (2.54 GiB)
backend/    FastAPI app: supervisor, skill loader, llama client, hardware
            profiles, SQLite chat storage, 79 tests
frontend/   index.html + app.js + style.css — vanilla JS, no build step
rag/        extractor → chunker → BM25 retriever; SQLite document store
skills/     15 markdown skills (system prompts + routing triggers)
config/     model.json, runtime.json, hardware.json, profiles/*.json
storage/    pocket_ai.db (chat history; created at runtime)
logs/       llama-server.log, backend.log, preflight.env
```

### Launcher (Phase 9)

`START_AI.bat` is the single entry point. Flow:

1. Resolve bundled Python (`runtime\python`) or fall back to system Python.
2. Run `preflight.py` (stdlib-only): Python version, required modules, config
   validity, required files (llama-server.exe, model, backend, frontend),
   RAM measurement, **hardware-profile selection**, port states.
3. Preflight writes `logs/preflight.env`; the bat reads it and exports
   `POCKETAI_PROFILE` and `POCKETAI_CTX`.
4. Start llama-server via `runtime\start_model.bat` (ctx from `POCKETAI_CTX`).
5. Start the backend minimized, wait for `/health`, open the browser.

The launcher is idempotent: re-running it when the stack is already up just
reports ready and opens the browser. `STOP_AI.bat` stops both processes via
path-matched process lookup (`stop_backend.ps1` matches command line **or**
executable path against the PocketAI root, so nothing outside PocketAI is
ever killed).

**Why the launcher selects the profile, not the backend:** by the time the
backend starts, llama-server has already allocated ~4.6 GB; a fresh RAM
measurement would wrongly downgrade the profile. Preflight measures *before*
anything loads and hands the decision down via environment variables. The
backend's own selection logic remains as fallback for manual starts.

### Backend (Phase 5)

FastAPI on bundled embeddable Python. Dependencies: `fastapi`, `uvicorn`,
`httpx` — everything else is stdlib. **No subprocess calls anywhere in the
backend.** Request flow through the supervisor
(`backend/supervisor/router.py`):

1. Vague-message gate → `requirement_interrogator` clarification questions
   (rule-based on purpose: a second model round-trip would double latency).
2. Skill selection — explicit request field > keyword-trigger matching >
   `general` fallback.
3. Conversation history from SQLite, trimmed to the live server context
   (read from `/props`) minus generation budget and safety margin.
4. Memory guard — refuse with 503 if free RAM < 1200 MB.
5. Inference — queued through a semaphore (server has 1 slot).
6. Validate, persist, return.

### RAG (Phase 7)

```
upload → extension/size check → extract text → chunk (1200 chars, 150 overlap)
       → store chunks in SQLite → BM25 index (rebuilt lazily on change)
```

- **BM25 instead of embeddings, deliberately.** An embedding model would need
  a second server process (~300–500 MB extra RAM) on 8 GB machines. BM25 is
  pure Python, needs only token lists in memory, and is a strong baseline for
  technical text. The seam stays open: `rag/embeddings/base.py` defines an
  `EmbeddingProvider` protocol for a future swap.
- SQLite (`rag/vector_store/documents.db`) is the single source of truth;

### Image input / OCR (image paste & upload)

```
paste/upload → base64 → validate (type + size) → normalise (downscale large,
upscale tiny) → OCR via bundled Tesseract → OCR text + metadata → model prompt
```

- Photos/screenshots are not sent to the model directly: Qwen3.5 is text-only,
  so the only viable offline path is OCR. The OCR engine is the **portable
  UB-Mannheim Tesseract** bundled under `runtime/ocr/` (binary + `tessdata/`),
  driven by `pytesseract` from the bundled Python. No network, no cloud.
- `backend/image/` holds the pipeline: `errors.py` (typed `ImageError` →
  HTTP 413/415/422/502/500), `ocr.py` (`run_ocr`, PSM 3/6/11 fallback, TSV
  confidence), `processing.py` (`process_image`, decode/validate/normalise/
  OCR/attach). `ChatRequest` carries optional `image/image_name/image_type`
  base64; `image` makes `message` optional.
- Images are **not** persisted (no image bytes in SQLite). The `messages`
  table gains an `attachment` JSON column with metadata only (type/mime/name/
  dims/OCR availability + confidence) so the UI can re-show the thumbnail.
- Memory-safe by design: decode → validate → normalise to a temp PNG (immediately
  deleted) → OCR → text. A `max_pixels` guard prevents decompression bombs.
- If OCR fails or finds no text, the request still proceeds and the model is
  told extraction failed (graceful degradation, no crash).

  the index is derived, so the USB bundle stays copy-friendly.
- Chat integration: with the "docs" option enabled, the supervisor retrieves
  top chunks for the user message and injects them as context within a
  character budget.

### Frontend (Phase 6)

Vanilla HTML/CSS/JS served by the backend — no build step, no framework, no
CDN. Two tabs (Chat, Documents) plus live status pills (model state, active
profile, free RAM). XSS-safe by construction: all dynamic content goes
through `textContent`/`createElement`, never `innerHTML`. A test asserts the
frontend contains no external URLs.

### Skills (Phase 8)

Plain markdown files: `skills/<id>/skill.md` with frontmatter
(`name`, `description`, `triggers`) and a system-prompt body. Loaded
dynamically at startup; malformed entries are skipped, never fatal; symlink
escapes out of `skills/` are rejected. 15 ship today (general, debugging,
security_review, council, grill_me, …). Adding a skill = adding a folder.

## 4. Configuration: single source of truth

Nothing is hardcoded downstream. All paths are relative to the PocketAI root,
resolved from file location (`Path(__file__).resolve().parents[N]` in Python,
`%~dp0` in batch) — this is what makes the drive letter irrelevant.

| File | Owns |
|---|---|
| `config/model.json` | llama-server host/port (8091), model file, alias, local API key, timeout |
| `config/runtime.json` | backend host/port (8090), relative paths, chat limits, `enable_thinking`, security switches |
| `config/hardware.json` | profile selection rules (RAM bands, free-RAM thresholds) |
| `config/profiles/*.json` | one file per profile (stem = id): `safe`, `normal`, `performance` |

## 5. Memory protection (the core 8 GB problem)

The model server peaks near 4.6 GB; Windows needs ~2.5–3 GB. That leaves
under 1 GB headroom on an 8 GB PC, so RAM is managed explicitly:

| | SAFE | NORMAL | PERFORMANCE |
|---|---|---|---|
| max_generation_tokens | 768 | 1536 | 2048 |
| history_budget_tokens | 1200 | 2500 | 4000 |
| recommended_server_context | 4096 | 8192 | 8192 |

Selection: total < 7 GB → safe; 7–12 GB → normal iff ≥ 4000 MB free;
> 12 GB → performance iff ≥ 6000 MB free and the profile exists, else normal;
unknown → safe. Additionally, **every** `/chat` request is refused with 503
when free RAM < 1200 MB — the backend degrades to an error instead of letting
the machine swap-thrash.

## 6. Key decisions and evidence

| Decision | Why | Evidence |
|---|---|---|
| llama.cpp CPU build, `-np 1` | 8 GB iGPU-only targets; 1 slot saves RAM | Phase 4: default 4 slots wasted RAM; peak 4.6 GB with 1 slot |
| Qwen3.5-4B Q4_K_M | Best quality that fits 8 GB | 2.54 GiB weights, loads in 6.5 s, ~9 t/s |
| Thinking OFF by default | Thinking consumed the whole budget → empty answer | Live A/B: thinking on = 1536 tok/167 s/empty; off = 683 tok/68 s/full answer |
| `-t` physical / `-tb` logical threads | SMT hurts generation, helps prompt processing | llama-bench: tg 9.81 vs 7.11 t/s; pp 38.86 vs 48.73 t/s |
| BM25, not embeddings | Second model process doesn't fit in 8 GB | RAM math; retrieval quality acceptable for technical corpora |
| Router, not agent framework | At ~9 t/s, extra model round-trips are unaffordable | Rule-based clarification gate costs 0 tokens |
| Embeddable Python | Locked-down PCs: no installs allowed | Bundled 3.13.14 runs with `PYTHONNOUSERSITE=1` isolation |
| Vanilla frontend | No build step can break on a locked-down PC | 3 files, served by backend, no external URLs |
| Port 8090/8091 | 8080 squatted (Oracle TNS on a dev PC), avoids Ollama/LM Studio | Preflight detects ours/free/busy per port |

## 7. What the backend is NOT

- Not an agent framework: no autonomous tool loops, no self-modifying plans.
- Not a multi-user service: one slot, one user, loopback only.
- Not a general server: it refuses non-loopback binds at startup and never
  spawns subprocesses.
