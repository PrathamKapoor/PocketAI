# PocketAI

> A fully portable, **offline** AI assistant that lives on a USB drive.
> Plug it into a restricted college PC and turn it into a useful AI workstation —
> no internet, no accounts, no API keys, no installs, no admin rights.

 Product docs:
[overview](docs/PRODUCT_OVERVIEW.md) · [demo guide](docs/DEMO_GUIDE.md).

---

## The problem

College computers are locked down. Students can't install software, can't reach
cloud AI, can't create accounts, and can't run GPUs. Yet they need help studying,
coding, reading PDFs, and understanding errors.

## The solution

PocketAI is the **"Linux Live USB of AI"**: a self-contained intelligence
environment you carry in your pocket. The host PC does the inference; the USB
only stores the system. Everything is drive-letter agnostic and runs in
userspace. Double-click `START_AI.bat`, a browser opens, done.

> **The model is not the product. The system around the model creates the intelligence.**

PocketAI is organized like a lightweight AI organization, not a chatbot:

```
User → Supervisor → Skills → Tools → Local Model
```

## Features

- **One-click start/stop** — `launcher\START_AI.bat` runs preflight checks,
  picks a hardware profile for the machine, starts the model server and
  backend, opens the browser. Idempotent; survives drive-letter changes.
- **Mode-based orchestration** — users pick a mode, not a skill: ✨ Auto
  (default), ⚡ Quick, ⚙ Normal, 💻 Coding, 📚 Study, 🔍 Research,
  🏗 Engineering, 🧠 Deep Analysis. Each mode runs a pipeline of the 15
  internal specialist skills as one composed model call, always ending
  with a mandatory Response Formatter. Skills are plain markdown files —
  add a folder, restart, done.
- **Quality layer** — direct answer first, comparison tables for
  recommendations, no consultant-style filler. Internal workflows, skill
  names and review reports are never shown (an optional developer mode
  reveals them). A **Just answer** button skips every workflow. Preference
  persists locally.
- **Personal knowledge base (RAG)** — upload PDF/TXT/MD/PPTX (up to 25 MB),
  chat with the "docs" option ticked, or search chunks directly. BM25
  retrieval over SQLite: no embedding model, no extra RAM.
- **Memory-safe on 8 GB PCs** — three hardware profiles (safe/normal/
  performance) selected from live RAM, plus a per-request guard that refuses
  with a clear error instead of swap-thrashing the machine.
- **Fully offline** — zero runtime network dependencies; loopback-only
  services; no telemetry.
- **Browser dashboard** — vanilla HTML/CSS/JS (no build step, no CDN),
  live status pills, first-run welcome with hardware status, a **Demo** tab
  (sample documents, showcase prompts, sample conversation) and an **About**
  tab (version, live system info, modes, limitations).
- **Portable by construction** — verified: fresh copy boots, drive letter
  changes work, chat history and documents travel with the drive.

## Architecture

```
Browser ──► FastAPI backend (127.0.0.1:8090) ──► llama-server (127.0.0.1:8091)
            │ serves frontend, supervisor,          │ Qwen3.5-4B Q4_K_M, CPU-only
            │ skills, RAG, SQLite chat history      │ 1 slot, Bearer-key protected
            └ bundled embeddable Python 3.13        └ llama.cpp b10549, bundled DLLs
```

| Layer | Choice | Why |
|---|---|---|
| Inference | llama.cpp `llama-server` (OpenAI-compatible) | Portable binaries, CPU-first, no install |
| Model | Qwen3.5-4B Instruct, Q4_K_M (hybrid SSM+attention) | Best quality that fits 8 GB (2.54 GiB, peaks 4.6 GB) |
| Backend | FastAPI on **embeddable** Python 3.13 | Orchestration, skills, RAG — no install on host |
| Frontend | Vanilla HTML/CSS/JS, served by the backend | Lightest reliable option; no build step can break |
| RAG | Extract → chunk → **BM25** over SQLite | An embedding model doesn't fit in 8 GB; seam kept for later |
| Storage | SQLite (WAL) | Zero-config, single file, copy-friendly |
| Launcher | Batch + PowerShell + stdlib preflight | Works on locked-down PCs with stripped `PATH` |

Full detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
Per-phase build docs: [runtime](docs/runtime_setup.md) ·
[backend](docs/backend_setup.md) · [RAG](docs/rag_setup.md) ·
[launcher](docs/launcher_setup.md)

## Repository layout

```
pocket_ai/
├── launcher/       # START_AI.bat, STOP_AI.bat, preflight.py, build_runtime.py
├── runtime/        # llama.cpp build + bundled embeddable Python (build artifacts)
├── models/         # Qwen3.5-4B Q4_K_M GGUF (2.54 GiB)
├── backend/        # FastAPI app: supervisor, skills, llama client, profiles, tests
├── frontend/       # index.html + app.js + style.css (no build step)
├── rag/            # extractor → chunker → BM25 retriever + SQLite document store
├── skills/         # 15 markdown skill modules
├── config/         # model/runtime/hardware JSON + profiles/
├── storage/        # chat database (created at runtime)
├── logs/           # llama-server.log, backend.log, preflight.env
└── docs/           # verified setup guides + final documentation
```

## Technology

- **llama.cpp b10549** (official win-cpu-x64 release), `-np 1`, flash
  attention, physical/logical thread split — measured **~9–10 tok/s
  generation, ~49.5 tok/s prompt processing**, 6.5 s cold start.
- **Qwen3.5-4B** (qwen35 architecture, hybrid SSM + attention), thinking
  mode off by default (measured: thinking ate the whole budget → empty
  answer).
- **Python 3.13 embeddable** + FastAPI/uvicorn/httpx; everything else stdlib
  (sqlite3, zipfile, ctypes for RAM/CPU detection — no psutil).
- **110 tests** with a mocked llama-server (`httpx.MockTransport`): config,
  skills, chat flows, mode orchestration, response quality layer, RAG,
  storage, hardware profiles, frontend guarantees.

## Demo

```
1. Plug in the USB drive (any letter).
2. Double-click  launcher\START_AI.bat
3. A browser opens at http://127.0.0.1:8090/  — chat, upload documents,
   tick "docs" and ask questions about your material. First launch shows a
   welcome overlay; the Demo tab has sample documents, prompts, and a
   sample conversation.
4. Double-click  launcher\STOP_AI.bat  when done (before ejecting).
```

Requirements: Windows 10/11 x64, 8 GB RAM minimum. No internet, no admin
rights. A guided 5-minute walkthrough: [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md).
See also [docs/USER_GUIDE.md](docs/USER_GUIDE.md) and
[docs/INSTALLATION.md](docs/INSTALLATION.md).

## Constraints honored

- No internet, no API keys, no HF login at runtime.
- No Ollama / Python / Node / Docker installs on the host.
- No administrator privileges; pure userspace.
- Drive-letter independent (verified E: → G:). All paths relative.
- Optimized for the **weakest** target: i5 11/12th-gen, 8 GB RAM, iGPU only.

## Documentation

| Doc | Contents |
|---|---|
| [docs/PRODUCT_OVERVIEW.md](docs/PRODUCT_OVERVIEW.md) | What PocketAI is, who it is for, honest boundaries |
| [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md) | A guided 5-minute demo, with reset instructions |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, data flow, design decisions with evidence |
| [docs/ARCHITECTURE_DIAGRAM.md](docs/ARCHITECTURE_DIAGRAM.md) | Component and request-flow diagrams (incl. modes) |
| [docs/INSTALLATION.md](docs/INSTALLATION.md) | User setup + building a drive from source |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Daily use, modes, skills, documents, troubleshooting |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, trade-offs, hardening details |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | All measured numbers (inference, RAM, RAG) |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | Honest boundaries and known trade-offs |

## Future improvements

- **OCR** (portable Tesseract) for scanned PDFs and screenshots → text → model.
- **Semantic retrieval** — a small local embedding model behind the existing
  `EmbeddingProvider` seam, if a ≤ 300 MB-RAM option matures.
- **Log rotation** and an optional watchdog/auto-restart.
- **Conversation export** (markdown/JSON) from the UI.
- **GPU/Vulkan build path** for machines that allow it.
- **Model upgrades** — re-verify newer Qwen releases against the RAM budget.
- **Backend auth option** for users who want defense against DNS-rebinding
  on shared machines.
