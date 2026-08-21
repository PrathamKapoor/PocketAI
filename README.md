# PocketAI

> A fully portable, **offline** AI assistant that lives on a USB drive.
> Plug it into a restricted college PC and turn it into a useful AI workstation —
> no internet, no accounts, no API keys, no installs, no admin rights.

**Status:** `Phase 3 / 10` — architecture finalized, repository scaffolded. See [documentation/ROADMAP.md](documentation/ROADMAP.md).

---

## The problem

College computers are locked down. Students can't install software, can't reach
cloud AI, can't create accounts, and can't run GPUs. Yet they need help studying,
coding, reading PDFs, and understanding errors.

## The solution

PocketAI is the **"Linux Live USB of AI"**: a self-contained intelligence
environment you carry in your pocket. The host PC does the inference; the USB
only stores the system. Everything is drive-letter agnostic and runs in userspace.

## Core philosophy

> **The model is not the product. The system around the model creates the intelligence.**

PocketAI is organized like a lightweight AI organization, not a chatbot:

```
User → Supervisor → Skills → Tools → Local Model
```

---

## Architecture (overview)

| Layer      | Choice                                   | Why |
|------------|------------------------------------------|-----|
| Inference  | llama.cpp (`llama-server`, OpenAI-compatible) | Portable single binary, CPU-first |
| Model      | Qwen3.5-4B Instruct, Q4_K_M (hybrid SSM+Attn) | Fits 8GB RAM, small KV cache |
| Backend    | FastAPI on **embeddable** Python (no install) | Orchestration, skills, RAG |
| Frontend   | Vanilla HTML/CSS/JS (no build step)      | Lightest reliable option |
| RAG        | Extract → Chunk → Embed → SQLite vectors → Qwen | Offline, lightweight |
| OCR        | Tesseract (portable)                     | Screenshot → text → Qwen reasoning |
| Storage    | SQLite                                   | Zero-config, single file |

Full detail in [documentation/ARCHITECTURE.md](documentation/ARCHITECTURE.md).

## Repository layout

```
pocket_ai/
├── runtime/        # Bundled llama.cpp + embeddable Python (assembled at build time)
├── models/         # Qwen3.5-4B Q4_K_M GGUF (+ optional small embedding model)
├── backend/        # FastAPI app: supervisor, skill loader, RAG, tools
├── frontend/       # Vanilla JS dashboard (served by backend)
├── rag/            # Ingestion pipeline + SQLite vector store (data lives here)
├── skills/         # Markdown/YAML skill modules (loaded on demand)
├── agents/         # Supervisor + orchestration definitions
├── tools/          # Portable tools (Tesseract OCR, etc.)
├── config/         # Hardware profiles + runtime settings
├── launcher/       # START_AI / STOP_AI launchers (hardware detect → start → open browser)
├── logs/           # Runtime logs
└── documentation/  # Architecture, roadmap, decisions, security review
```

## Running (target experience)

```
1. Plug in the USB drive.
2. Double-click  START_AI.exe   (or START_AI.bat)
3. A browser opens at the PocketAI dashboard. Done.
```

To stop: `STOP_AI.bat`.

---

## Constraints honored

- No internet, no API keys, no HF login at runtime.
- No Ollama / Python / Node / Docker installs on the host.
- No administrator privileges; pure userspace.
- Drive-letter independent (works as E:, F:, G:, …). All paths relative.
- Optimized for the **weakest** target: i5 11/12th-gen, 8GB RAM, iGPU only.

## Limitations & future work

Tracked in [documentation/ROADMAP.md](documentation/ROADMAP.md#future-improvements).
