# PocketAI User Guide

PocketAI is an offline AI assistant that runs entirely from your USB drive.
This guide assumes the drive is already prepared (see
`docs/INSTALLATION.md`).

---

## 1. Starting and stopping

| Action | How |
|---|---|
| Start | Double-click `launcher\START_AI.bat`. A console window appears, your browser opens automatically. |
| Stop | Double-click `launcher\STOP_AI.bat`. |
| Eject the USB | Always run STOP_AI first, then eject. |

Startup takes a few seconds (model load ~7 s on a modern CPU). Re-running
START_AI while PocketAI is already running is harmless — it detects the
running stack and just opens the browser.

The header shows three live status pills:

- **model** — `ready` (green) or the model server's state
- **profile** — the hardware profile selected for this PC
  (`safe` / `normal` / `performance`)
- **RAM** — free memory; if it drops too low, new messages are refused with a
  clear error instead of freezing the machine

## 2. Chat tab

Type a message, press **Enter** (Shift+Enter for a new line), or click Send.
Messages are limited to 8000 characters.

Every answer passes a built-in quality layer: direct answer first, a short
explanation, tables or bullets when they help, and a clear recommendation
for "which should I pick?" questions. No filler, no fake consulting
reports, no internal process shown.

### Response modes

The dropdown in the composer picks a **mode** — you choose the kind of
help you want, and PocketAI runs the right workflow behind the scenes.
Your choice is remembered in the browser.

| Mode | What it does |
|---|---|
| ✨ Auto (default) | Classifies your message with a zero-cost keyword check and runs the matching mode. Simple messages are never interrupted. |
| ⚡ Quick | Fast, direct answers. No clarifying questions — reasonable assumptions instead. "hey" gets a greeting, not an interrogation. |
| ⚙ Normal | Everyday assistant answers with a fact-check pass. Questions only when information is genuinely missing. |
| 💻 Coding | Debugging, code review and fixes; security review joins in when relevant. Never interrogates — it answers. |
| 📚 Study | Explanations, study plans and documentation in a natural teaching voice. |
| 🔍 Research | Structured analysis with fact-checking and multiple perspectives, delivered as a clean answer. |
| 🏗 Engineering | Full design workflow (requirements, architecture, security, production readiness, council) — delivered as one clean answer, not a report. |
| 🧠 Deep Analysis | Maximum depth: research, multi-perspective review and synthesis for hard questions. |

Notes:

- Every mode runs its workflow as **one composed model call** and always
  ends with the Response Formatter: direct answer first, no internal
  process, no skill names, no fake consulting reports.
- The meta line under each reply shows `PocketAI • <Mode> Mode` plus speed
  info. Enable **Developer mode** in the About tab to also see which
  internal stages ran.
- Only Engineering and Deep Analysis ask clarifying questions for very
  short/vague messages ("help", "start"). Every other mode always answers.
- The **Just answer** button (next to Send) skips every workflow and sends
  the message straight through — use it whenever you want output, not
  process.

### Skills (internal)

PocketAI ships 15 specialist skills (debugging, architecture review,
security review, council, …). You never pick them directly — each mode
combines the skills it needs into one pipeline, and Auto picks the mode
for you. Specialist skills outside the standard pipelines (prompt
engineering, performance, demo scripts, stress-testing, after-action
reviews) join a pipeline automatically when your message asks for them.

### Vague messages

In every mode except Engineering and Deep Analysis, short or vague
messages ("help", "hi") go straight to the model — you always get an
answer. In Engineering and Deep Analysis only, PocketAI instead returns
clarifying questions instantly (no generation time spent); answer them
and send again.

### Using your documents in chat

Tick the **docs** checkbox next to the input. PocketAI then retrieves the
most relevant chunks from your uploaded documents (see below) and injects
them into the prompt, with source filenames shown. Best for questions about
material you uploaded — lecture slides, PDFs, notes.

### Conversations

Each new message without history starts a new conversation, stored in the
database on the drive. Follow-ups in the same session keep context until the
history budget for your hardware profile is reached (older messages are
trimmed automatically, not lost — they stay in the database).

### Speed expectations

On a typical college PC expect roughly **5–10 tokens/second** — a solid
paragraph takes about a minute. Longer answers take longer; the `safe`
profile caps answers shorter than `performance` to protect low-RAM machines.

## 3. Documents tab

Your personal knowledge base, stored on the USB drive.

1. **Upload** — click Choose Files (PDF, TXT, MD, MARKDOWN, PPTX; up to
   25 MB each), then Upload. Text is extracted and indexed immediately;
   large files can take a few seconds (a 600-page PDF ≈ 15 s).
2. **List** — the table shows every indexed document with size and chunk
   count. Delete removes a document and its chunks.
3. **Search** — keyword search over all chunks, ranked by BM25 relevance.
   Use it to check what PocketAI actually sees from your files.

Notes:

- PDFs are capped at 1000 pages, PPTX at 500 slides (text runs only —
  speaker notes and images are not extracted).
- Scanned/image-only PDFs yield no text from the PDF itself. If you paste or
  upload a *screenshot* of that page, PocketAI's image input (below) will OCR
  it instead.
- Retrieval is keyword-based (BM25), not semantic: search/chat works best
  when your question shares vocabulary with the document.

## 3b. Sending images (paste a screenshot)

PocketAI reads pictures through **OCR** — the local Qwen model is text-only, so
a screenshot or photo becomes text the assistant can reason about. No image
bytes leave your machine and nothing is uploaded to the cloud.

**How to send an image**

- **Paste from clipboard** — copy an image (e.g. `PrtScn`, Snipping Tool,
  or right-click → Copy Image) and paste it into the chat box
  (`Ctrl+V`). Pasted text still works as before.
- **Upload a file** — click the image chip (📎) and choose a `PNG`, `JPG`,
  `JPEG`, `WEBP`, `BMP`, or `GIF`. Animated GIFs use the first frame.

Once attached you'll see a thumbnail with the filename. Remove it with the
(✕) before sending if you change your mind. Then type an optional question
and press Enter (or click Send). You can send an image **with or without**
typed text — an image alone is enough.

**What happens**

1. The image is decoded and validated (type + size).
2. Very large images are downscaled; tiny ones are upscaled so OCR can read
   the text. The original is never stored.
3. The bundled, portable Tesseract engine (offline) reads the text.
4. That text is shown as context to the model alongside your message; the
   model answers based on what the OCR extracted.

**Limits and behaviour**

- Max upload **10 MB**; accepted types listed above. Oversize or unsupported
  types are rejected with a clear message.
- OCR works best on **legible text**. Blurry, very low-contrast, or tiny
  fonts may not be recognised — the assistant will say the text extraction
  failed and ask you to describe the image instead, rather than guessing.
- The image itself is **not** saved. Only a small metadata note (that an
  image was attached, its type, and OCR confidence) is kept in the chat
  history so the thumbnail can be shown later.

## 4. Demo tab

Everything needed to show PocketAI without preparing material:

- **Sample documents** — one click indexes two small files (study notes and
  a project brief) into the knowledge base. Then ask about them in chat
  with the "docs" checkbox ticked.
- **Showcase prompts** — clickable prompts, one per mode (quick answer,
  coding, study, research, engineering, deep analysis, auto). Clicking
  places the prompt in the chat input and sets the matching mode — nothing
  is sent automatically.
- **Sample conversation** — an illustrative exchange written for the demo
  (labelled as such, not live model output).

A full guided walkthrough: `docs\DEMO_GUIDE.md`.

## 5. About tab

Version, live system status (backend, model server, hardware profile,
server context, RAM, CPU, Python), the response-mode reference, the
**Developer mode** toggle (shows internal workflow stages in chat), known
limitations, and pointers to the documentation.

## 6. Thinking mode (advanced)

Qwen3.5 is a thinking model. PocketAI ships with thinking **off** because
live testing showed reasoning tokens can consume the entire generation
budget and return an empty answer. To enable it, set
`chat.enable_thinking: true` in `config\runtime.json` and restart the
backend. Answers become slower but include a `reasoning` trace.

## 7. Troubleshooting

| Symptom | What to do |
|---|---|
| START_AI window closes with `PREFLIGHT FAILED: …` | Read the reason shown. Missing model file → restore `models\*.gguf`. Port in use → close the other program or change ports in `config\` (see `docs\launcher_setup.md`). |
| Browser opens but model pill is not green | Model is still loading (watch `logs\llama-server.log`) or failed to start. Re-run START_AI. |
| "Not enough free memory" (HTTP 503) on send | Close other programs (especially browser tabs). The RAM guard refuses rather than risk freezing an 8 GB PC. |
| Answers cut short | Normal on `safe` profile — the generation budget is deliberately small. Also happens if thinking mode is enabled. |
| Slow answers | Expected on CPU (~5–10 tok/s). Shorter questions and `safe`/`normal` contexts respond faster. |
| Nothing happens on send | Check the backend console window for errors; check `logs\backend.log`. |
| Want a fresh start | Stop the stack, delete `storage\pocket_ai.db` (chat) and/or `rag\vector_store\documents.db` + `rag\uploads\` (documents). |

## 8. Where things live

| Data | Location |
|---|---|
| Chat history | `storage\pocket_ai.db` |
| Uploaded documents + index | `rag\uploads\`, `rag\vector_store\documents.db` |
| Logs | `logs\llama-server.log`, `logs\backend.log`, `logs\preflight.env` |
| Settings | `config\*.json` (edit while stopped) |

Everything persists on the drive — unplug, walk to another PC, plug in, and
your history and documents are still there.
