# PocketAI Architecture Diagram

> Visual companion to [ARCHITECTURE.md](ARCHITECTURE.md). All claims are
> verified against the running system or the test suite (109 passed,
> 1 skipped).

---

## 1. System context

```
┌─────────────────────── host PC (nothing installed) ───────────────────────┐
│                                                                           │
│   ┌─────────┐   HTTP    ┌──────────────────────┐   HTTP    ┌───────────┐ │
│   │ Browser │ ────────► │ FastAPI backend      │ ────────► │ llama-    │ │
│   │ (user)  │  127.0.0.1│ 127.0.0.1:8090       │  127.0.0.1│ server    │ │
│   └─────────┘   :8090   │                      │   :8091   │ (CPU,     │ │
│                         │ • serves frontend    │  Bearer   │  1 slot)  │ │
│                         │ • supervisor + modes │  key      │ Qwen3.5-4B│ │
│                         │ • skills, RAG        │           │ Q4_K_M    │ │
│                         │ • SQLite history     │           └───────────┘ │
│                         └──────────────────────┘                         │
│                          bundled embeddable Python 3.13                  │
└─────────────────────────── all files on the USB drive ────────────────────┘
        No internet. No outbound calls. Loopback only, enforced.
```

The browser talks only to 8090. The backend is the only client of 8091.

## 2. Request flow: POST /chat (mode-based orchestration)

```
Browser
  │  { message, conversation_id?, mode?, use_documents? }
  ▼
┌────────────────────────── Supervisor (backend/supervisor/router.py) ─────┐
│                                                                          │
│  1. RESOLVE MODE                                                         │
│     mode = resolve_mode(requested, message)                              │
│       • none given        → auto (default)                               │
│       • quick/normal/     → as requested                                 │
│         coding/study/research/                                           │
│         engineering/deep                                                 │
│       • just_answer       → skip ALL workflows                           │
│       • auto              → classify_intent(message)                     │
│                             keyword signals → coding/study/research/     │
│                             engineering/deep · short → quick ·           │
│                             else normal (zero model tokens)              │
│  ▼                                                                       │
│  2. VAGUE GATE  (engineering/deep only)                                  │
│     short/vague message → requirement_interrogator clarification,        │
│     returned immediately (0 model calls). All other modes always answer. │
│  ▼                                                                       │
│  3. PIPELINE PROMPT                                                      │
│     just_answer        → JUST ANSWER system prompt                       │
│     otherwise          → build_pipeline_prompt(mode): ONE composed       │
│                          model call with stage headers + truncated       │
│                          stage bodies, ALWAYS ending in the mandatory    │
│                          Response Formatter.                             │
│                   quick:       General Assistant → Stop Slop             │
│                   normal:      General Assistant → Fact Checker          │
│                   coding:      Requirement Interrogator (light) →        │
│                                Debugging → Code Reviewer → Security      │
│                                Review (if needed)                        │
│                   study:       Study Tutor → Documentation → Humanizer   │
│                   research:    Research Analyst → Fact Checker →         │
│                                Council → Stop Slop                       │
│                   engineering: Interrogator → Architecture → Security →  │
│                                Production → Council → Stop Slop          │
│                   deep:        Superpower → Research Analyst → Council   │
│     (+ best-matching orphan skill injected before the formatter          │
│        when its activation triggers match the message)                   │
│  ▼                                                                       │
│  4. HISTORY   SQLite → trimmed to server context − budget − margin       │
│  ▼                                                                       │
│  5. RAG (if use_documents)  BM25 top-k chunks appended to system prompt  │
│  ▼                                                                       │
│  6. MEMORY GUARD   free RAM < 1200 MB → refuse 503                       │
│  ▼                                                                       │
│  7. INFERENCE   queued semaphore (server has 1 slot) → llama-server      │
│  ▼                                                                       │
│  8. VALIDATE → PERSIST (SQLite) → RESPOND                                │
└──────────────────────────────────────────────────────────────────────────┘
  │  { answer, mode, workflow?, clarification, reasoning?, timings }
  ▼
Browser renders bubble + meta line:  PocketAI • <Mode> Mode · tokens/time
(internal workflow stages shown only when developer mode is enabled)
```

## 3. Components

```
launcher/   START_AI.bat ─► preflight.py ─► start model ─► start backend ─► browser
                               │ measures RAM BEFORE the model loads,
                               └ picks profile → logs/preflight.env

runtime/    llama.cpp b10549 (win-cpu-x64) + embeddable Python 3.13
models/     Qwen3.5-4B-Q4_K_M.gguf (2.54 GiB)

backend/
├── main.py              FastAPI app: /health /system /chat
│                        /skills (developer mode only)
│                        /documents/* /search + static frontend
├── supervisor/
│   ├── router.py        the flow above
│   └── pipelines.py     modes, pipelines, classifier, prompt builder
├── skills/loader.py     markdown skills → registry; orchestration metadata
├── tools/llama_client.py  httpx client, queue, retries, error mapping
├── tools/sysinfo.py     RAM/CPU via stdlib ctypes
├── hardware/profiles.py SAFE | NORMAL | PERFORMANCE selection
├── storage/db.py        SQLite (WAL): conversations, messages
└── tests/               110 tests, mocked llama-server (httpx.MockTransport)

rag/        extractor (pdf/txt/md/pptx) → chunker (1200 ch / 150 overlap)
            → BM25 retriever → SQLite document store
frontend/   index.html + app.js + style.css — vanilla JS, textContent-only
skills/     15 × skills/<id>/skill.md (frontmatter: name, description,
            triggers, category, priority, modes, activation)
config/     model.json · runtime.json · hardware.json · profiles/*.json
storage/    pocket_ai.db (chat)          logs/  llama-server.log, backend.log
```

## 4. Data stores

```
storage/pocket_ai.db            conversations + messages (+ resolved mode
                                id stored in the legacy skill column)
rag/vector_store/documents.db   documents + chunks (single source of truth;
                                the BM25 index is derived, rebuilt lazily)
rag/uploads/                    original uploaded files
config/*.json                   all runtime parameters (single source of truth)
browser localStorage            pocketai.mode, pocketai.devmode,
                                pocketai.welcomed (UI prefs)
```

Everything persists on the drive; unplug, move to another PC, plug in —
history and documents travel with it.

## 5. Memory budget (the core 8 GB problem)

```
8 GB total
├── ~2.5–3.0 GB  Windows + desktop
├── ~4.6 GB      llama-server peak (1 slot, 4–8k context)
└── < 1.0 GB     headroom  ← guarded:
                   • profile picked from live RAM at preflight
                   • every /chat refused at < 1200 MB free (503)
                   • generation/history budgets capped per profile
```

| | SAFE | NORMAL | PERFORMANCE |
|---|---|---|---|
| max_generation_tokens | 768 | 1536 | 2048 |
| history_budget_tokens | 1200 | 2500 | 4000 |
| recommended_server_context | 4096 | 8192 | 8192 |

## 6. What the backend is NOT

- Not an agent framework: no autonomous tool loops; every mode pipeline is
  a single composed model call (latency/RAM budget forbids round-trips at
  ~9 t/s).
- Not multi-user: one slot, one user, loopback only.
- Not a general server: refuses non-loopback binds at startup; never spawns
  subprocesses.
