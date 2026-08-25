# PocketAI Backend Setup (Phase 5)

Verified backend layer: FastAPI on 127.0.0.1:8090, supervisor request router,
dynamic skill loading, hardware profiles, SQLite storage. All measurements
below were taken live against the Phase 4 runtime (llama-server on
127.0.0.1:8091, Qwen3.5-4B Q4_K_M, CPU-only).

```
Browser UI → FastAPI Backend (:8090) → Supervisor → Skills / Tools → llama-server (:8091)
```

---

## 1. Components

```
backend/
├── main.py               # FastAPI app + endpoints; refuses non-loopback bind
├── schemas.py            # Pydantic request/response models (validation)
├── config/loader.py      # Reads config/*.json → typed PocketAIConfig
├── supervisor/router.py  # The request router (deliberately NOT an agent framework)
├── skills/loader.py      # Dynamic skill loading from skills/<name>/skill.md
├── storage/db.py         # SQLite (stdlib sqlite3, WAL): conversations, messages, settings, metadata
├── tools/llama_client.py # Async client for llama-server; queues requests (server runs -np 1)
├── tools/sysinfo.py      # RAM/CPU via stdlib ctypes (no psutil) + /proc fallback
├── tools/hardware.py     # SAFE/NORMAL/PERFORMANCE profile selection + per-request RAM guard
└── tests/                # 79 tests, mocked llama-server via httpx.MockTransport
```

Dependencies (see `backend/requirements.txt`): `fastapi`, `uvicorn`, `httpx`.
Everything else is Python stdlib. No subprocess calls anywhere in the backend.

## 2. Configuration (source of truth)

JSON files under `config/`; nothing is hardcoded downstream. All paths
are **relative to the PocketAI root** (resolved from file location, so the USB
drive letter can change).

| File | Owns |
|---|---|
| `config/model.json` | llama-server connection: host, port (8091), alias `qwen3.5-4b`, local API key, timeout (Phase 4) |
| `config/hardware.json` | Profile selection rules (RAM bands, free-RAM thresholds) |
| `config/profiles/*.json` | One file per hardware profile (file stem = profile id): `safe`, `normal`, `performance` |
| `config/runtime.json` | Backend host/port (8090), relative paths, chat limits, `enable_thinking`, security switches |

## 3. Hardware profiles (memory protection)

| | SAFE | NORMAL | PERFORMANCE |
|---|---|---|---|
| For | Unknown / beat-up 8 GB PCs | Clean 8 GB+ systems | Dev / high-memory machines (> 12 GB) |
| max_generation_tokens | 768 | 1536 | 2048 |
| history_budget_tokens | 1200 | 2500 | 4000 |
| parallel_requests | 1 | 1 | 1 (server has 1 slot; bigger profiles buy context budget, not parallelism) |
| recommended_server_context | 4096 | 8192 | 8192 |

Profiles live in `config/profiles/<id>.json` (file stem = id); `safe` and
`normal` are required, `performance` is optional. Selection rules live in
`config/hardware.json`.

Selection (`backend/tools/hardware.py`):

- total RAM < 7 GB → `safe`
- 7–12 GB → `normal` only if ≥ 4000 MB free at startup, else `safe`
- > 12 GB → `performance` if ≥ 6000 MB free **and** the profile exists,
  else `normal`
- unknown platform (RAM unreadable) → `safe` for budgeting, but inference is
  not blocked by the guard

**Launcher override (Phase 9):** `launcher\START_AI.bat` runs preflight
*before* the model loads, selects the profile there, and exports it as
`POCKETAI_PROFILE`. The backend honors that env var instead of re-measuring
RAM — necessary because by the time the backend starts, llama-server has
already consumed ~4.6 GB and a fresh measurement would wrongly downgrade the
profile. An explicit memory measurement (tests) always wins over the env var;
unknown profile names in the env var are ignored. The launcher also exports
`POCKETAI_CTX` from the selected profile's `recommended_server_context`, which
`runtime\start_model.bat` uses for `--ctx-size`.

Per-request guard: if free RAM < 1200 MB when a `/chat` request arrives, the
backend refuses with HTTP 503 instead of risking a swap-thrash with the model
server (which peaks near 4.6 GB).

## 4. Endpoints

Localhost only. Binding any non-loopback host is refused at startup
(`security.require_loopback_bind`).

### `GET /health`

```json
{"status": "ok",
 "backend": {"status": "ok", "version": "0.5.0"},
 "model":   {"status": "ready", "alias": "qwen3.5-4b"},
 "runtime": {"status": "running", "host": "127.0.0.1", "port": 8091}}
```

`model.status` is `ready`, the server's own status string while it loads the
model, or `unavailable` (with `runtime.status: "stopped"`) when llama-server
is not running.

### `GET /system`

RAM (total/available MB), CPU (logical/physical cores, arch), the active
hardware profile, live model-server caps read from `GET /props`
(`context`, `parallel_slots`), Python version.

### `GET /skills`

Developer mode only (`security.developer_mode: true` in
`config/runtime.json`, off by default). Returns the loaded skills: `id`,
`name`, `description`. With developer mode off the endpoint answers 404 —
the skill registry is internal architecture, not a user-facing API, and the
dashboard never calls it.

### `POST /chat`

Request:

```json
{"message": "Explain this traceback: ...",
 "conversation_id": null,
 "skill": null}
```

- `message` — required, 1–16000 chars (hard cap `chat.max_message_chars` = 8000 enforced by the supervisor)
- `conversation_id` — omit to start a new conversation; 404 if unknown
- `skill` — explicit skill id; overrides keyword routing; 404 if unknown

Response:

```json
{"conversation_id": 2, "skill": "debugging",
 "answer": "...", "reasoning": null, "clarification": false,
 "warning": null,
 "timings": {"prompt_tokens": 267, "completion_tokens": 683,
             "prompt_ms": 5542.5, "predicted_ms": 68133.2}}
```

`reasoning` is populated only when thinking is enabled (see §7).
`warning` is set when the model returns no visible content.

Error codes: 422 validation, 404 unknown skill/conversation, 503 RAM guard,
502 llama-server error.

## 5. Supervisor flow

`backend/supervisor/router.py` — a lightweight request router, deliberately
not an agent framework:

1. **Requirement Interrogator gate** — vague messages (< 12 chars or in a
   known-vague set: "help", "hi", "ok", …) are answered with the
   `requirement_interrogator` skill's clarification questions and
   `clarification: true`. Rule-based on purpose: at ~9 tok/s a second model
   round-trip would double latency for every message.
2. **Skill selection** — explicit `skill` field wins, else keyword-trigger
   matching (most trigger hits wins), else the `general` fallback.
3. **Conversation + history** from SQLite.
4. **Prompt preparation** — skill body as system prompt + history trimmed to
   fit the live server context (read from `/props`, cached) minus generation
   budget and a safety margin.
5. **Memory guard** (see §3).
6. **Inference** — queued through a semaphore: the server runs `-np 1`.
7. **Validate response** — empty content → warning (thinking may have eaten
   the budget).
8. **Persist + return.**

## 6. Skills

Local files, loaded dynamically at startup — no hardcoded prompts:

```
skills/<skill_id>/skill.md
```

```markdown
---
name: Debugging
description: Analysis-first help with errors, bugs and tracebacks.
triggers: error, bug, traceback, exception
---

You are a debugging specialist. ...   ← the skill body becomes the system prompt
```

Rules enforced by the loader:

- `skill_id` must match `^[a-z0-9][a-z0-9_]{0,63}$`
- entries that are not directories, lack `skill.md`, fail to parse, or
  resolve outside `skills/` (symlink escape) are skipped, never fatal
- `triggers` is a comma-separated keyword list used for routing

Shipped skills (15): `general` (default), `requirement_interrogator`,
`prompt_engineer`, `debugging`, `architecture_review`, `security_review`,
`performance_optimizer`, `production_readiness`, `research_analyst`,
`demo_product`, `council`, `grill_me`, `aar`, `stop_slop`, `superpower`.
Add a skill by dropping in a folder and restarting the backend.

## 7. Thinking model handling (Phase 4 finding, verified live)

Qwen3.5 is a thinking model. Live smoke test on the dev machine:

| `enable_thinking` | Same "explain this TypeError" query | Result |
|---|---|---|
| `true` | 1536 tokens / ~167 s | budget exhausted on reasoning, **empty answer** |
| `false` | 683 tokens / ~68 s | full structured answer |

Default is therefore `chat.enable_thinking: false` in `config/runtime.json`.
The flag is sent explicitly in every request (`chat_template_kwargs`) because
the model's chat template only enables thinking when told to. Set it to
`true` to trade latency for deeper reasoning; `reasoning` is then returned
and persisted alongside the answer.

## 8. Storage

`storage/pocket_ai.db` (SQLite, WAL, foreign keys; created automatically).

| Table | Purpose |
|---|---|
| `conversations` | id, title, timestamps |
| `messages` | role (user/assistant/system), content, reasoning, skill, FK cascade |
| `settings` | key/value app settings |
| `metadata` | key/value runtime metadata |

Chat history lives here. Document/RAG storage is separate:
`rag/vector_store/documents.db` + uploaded files under `rag/uploads/`
(Phase 7, see `docs/rag_setup.md`).

## 9. Running

Preferred (Phase 9 launcher — preflight, profile selection, logs, browser):

```bat
launcher\START_AI.bat          :: one click: preflight → model (:8091) → backend (:8090) → browser
launcher\STOP_AI.bat           :: stops backend + model server
```

Manual (development):

```bat
runtime\start_model.bat        :: 1. start llama-server (:8091), wait until ready
python backend\main.py         :: 2. start the backend (:8090)
```

Then: `http://127.0.0.1:8090/` (browser UI) or `/health`.
Stop: Ctrl+C the backend, then `runtime\stop_model.bat`.

Manual mode requires Python with `fastapi`, `uvicorn`, `httpx`
(`pip install -r backend/requirements.txt`). On target machines the portable
embeddable Python is bundled at `runtime/python` (see `docs/launcher_setup.md`).

## 10. Tests

```bat
python -m pytest backend/tests -q     :: 78 passed, 1 skipped
```

Covers: config loading, skill loading/routing (incl. malformed and
symlink-escape cases), app startup, `/health` in all three runtime states,
`/system`, `/chat` end-to-end against a mocked llama-server (routing,
history, conversation continuation, clarification gate, reasoning, empty
content), SQLite storage, profile selection (three profiles, launcher env
override) and the RAM guard, RAG upload/search/delete flows, and the
frontend's no-external-URLs / XSS-safety guarantees. The one skipped test
requires `os.symlink` rights (Windows dev machines).

## 11. Security posture

- Loopback bind only, enforced at startup (`main()` exits otherwise).
- The model API key from `model.json` is used for outgoing requests only and
  never returned by any endpoint.
- Request validation via Pydantic (message length, skill id pattern).
- Uploads: extension allowlist (.pdf/.txt/.md/.markdown/.pptx), 25 MB size
  cap, sanitized filenames, random hex document ids; files are stored only
  under `rag/uploads/<doc_id>/` and deletion validates the id format before
  touching the filesystem.
- Skill files are only read from `skills/` below the PocketAI root; symlink
  escapes are rejected.
- No arbitrary command execution: the backend never spawns subprocesses.
  The assistant is analysis-first by skill design.
- SQLite is the only persistence; no network egress of any kind.
