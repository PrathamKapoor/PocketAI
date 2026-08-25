# PocketAI Product Overview

> What PocketAI is, who it is for, and what it deliberately is not.
> Version 1.0.0 — Phase 11 (product polish and demo readiness).

---

## One line

PocketAI is a **portable, fully offline AI assistant** that runs from a USB
drive on locked-down Windows PCs: no installs, no admin rights, no internet,
no accounts.

## The pitch

College and enterprise computers are locked down: no software installs, no
cloud AI, no GPUs. PocketAI is the "Linux Live USB of AI" — a self-contained
intelligence environment you carry in your pocket. The host PC does the
inference; the USB stores the system, the model, your documents, and your
history. Double-click `START_AI.bat`, a browser opens, done.

> **The model is not the product. The system around the model creates the intelligence.**

## Who it is for

| Person | What PocketAI gives them |
|---|---|
| Students on locked-down college PCs | Study help, coding help, PDF Q&A — without cloud AI or installs |
| Developers in restricted environments | Debugging, security review, and architecture skills, fully offline |
| Anyone in air-gapped settings | A private assistant where nothing may leave the machine |
| Portfolio reviewers | A complete, measured, honestly documented system — end to end |

## What you get (v1.0.0)

- **Mode-based orchestration** — users pick a mode, not a skill: ✨ Auto
  (default, zero-token keyword classifier), ⚡ Quick, ⚙ Normal, 💻 Coding,
  📚 Study, 🔍 Research, 🏗 Engineering and 🧠 Deep Analysis. Each mode
  runs a pipeline of the 15 internal specialist skills as **one composed
  model call**, always ending with a mandatory Response Formatter.
- **Quality layer** — direct answer first, comparison tables for
  recommendations, no consultant-style filler. Internal workflows, skill
  names and review reports are never shown (an optional developer mode
  reveals them). A **Just answer** button bypasses every workflow. Mode
  preference persists in the browser. Skills are plain markdown files.
- **Personal knowledge base (RAG)** — upload PDF/TXT/MD/PPTX (up to 25 MB),
  chat with the "docs" option ticked, or search chunks directly. BM25 over
  SQLite: no embedding model, no extra RAM.
- **First-run experience** — a welcome overlay on first launch with
  capabilities and live hardware status; never shown again afterwards.
- **Demo tab** — one-click sample documents, showcase prompts (clickable),
  and an illustrative sample conversation. See
  [DEMO_GUIDE.md](DEMO_GUIDE.md).
- **About tab** — version, live system status (backend, model server,
  profile, context, RAM, CPU), mode reference, known limitations, and doc
  pointers.
- **Memory-safe on 8 GB PCs** — three hardware profiles (safe/normal/
  performance) selected from live RAM, plus a per-request guard that refuses
  with a clear error instead of swap-thrashing the machine.
- **Fully offline and private** — loopback-only services, zero runtime
  network dependencies, no telemetry. Your conversations and documents never
  leave the drive.

## Honest boundaries

- **One user, one request at a time** — the model server runs a single slot
  to fit in 8 GB RAM; requests are queued.
- **CPU speed** — roughly 8–10 tokens/second on typical hardware; a solid
  paragraph takes about a minute.
- **A 4B model** — great for everyday study and coding tasks, not
  frontier-level reasoning.
- **Keyword retrieval** — RAG is BM25, not semantic search; questions work
  best when they share vocabulary with the documents.
- **No OCR** — scanned/image-only PDFs yield no text yet.

Full detail: [LIMITATIONS.md](LIMITATIONS.md).

## Quick facts

| | |
|---|---|
| Version | 1.0.0 (Phases 1–11 complete) |
| Model | Qwen3.5-4B Instruct, Q4_K_M (2.54 GiB), thinking off by default |
| Inference | llama.cpp b10549, CPU-only, 1 slot, ~9–10 tok/s |
| Backend | FastAPI on bundled embeddable Python 3.13 (127.0.0.1:8090) |
| Model server | 127.0.0.1:8091, Bearer-key protected, loopback only |
| Frontend | Vanilla HTML/CSS/JS served by the backend — no build step, no CDN |
| Target machine | Windows 10/11 x64, 8 GB RAM minimum, no admin rights |
| Tests | 110 (109 passed, 1 skipped) with a mocked model server |

## Where to go next

| I want to… | Read |
|---|---|
| See it in action in 5 minutes | [DEMO_GUIDE.md](DEMO_GUIDE.md) |
| Start using it daily | [USER_GUIDE.md](USER_GUIDE.md) |
| Understand how it works | [ARCHITECTURE.md](ARCHITECTURE.md), [ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md) |
| Audit safety and privacy | [SECURITY.md](SECURITY.md) |
| Check the measured numbers | [PERFORMANCE.md](PERFORMANCE.md) |
| Know what it can't do | [LIMITATIONS.md](LIMITATIONS.md) |
