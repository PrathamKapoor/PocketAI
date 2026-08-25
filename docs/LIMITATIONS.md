# PocketAI Limitations

Honest list, current as of Phase 10. Each item is a conscious trade-off for
the target environment (locked-down 8 GB Windows PCs, offline, USB) or a
known boundary worth stating.

---

## Hardware & performance

- **8 GB is tight.** Model server peaks at 4.6 GB + Windows ~2.5–3 GB leaves
  under 1 GB headroom. Mitigated by hardware profiles and the per-request
  RAM guard (503 instead of swap-thrash), but heavy multitasking on an 8 GB
  PC can still force refusals. Close browser tabs during long generations.
- **CPU-only, ~5–10 tokens/second.** No GPU path (targets are iGPU-only).
  Long answers take minutes; this is inherent to the hardware class.
- **Single inference slot** (`-np 1`). Concurrent `/chat` requests queue;
  the second user of a shared PC waits. By design — more slots multiply
  KV-cache RAM.
- **Thinking mode off by default.** Enabled, reasoning tokens can exhaust
  the generation budget and return an empty answer (measured). Enabling it
  trades latency for depth and can still come back empty on `safe` profile.

## Retrieval (RAG)

- **BM25 is lexical, not semantic.** Queries work best when they share
  vocabulary with the documents. Paraphrased or conceptual questions can
  miss relevant chunks. Embeddings were rejected for RAM reasons; the
  provider seam exists (`rag/embeddings/base.py`) for a future small
  embedding model.
- **Screenshots/images: OCR only, not vision.** Pasted or uploaded images are
  read by the bundled offline Tesseract engine and turned into text for the
  text-only Qwen model. The model never "sees" the picture. OCR works best on
  legible text; blurry, low-contrast, or very small fonts may not be read, in
  which case the assistant reports that extraction failed and asks you to
  describe the image. Max **10 MB**, PNG/JPG/WEBP/BMP/GIF. Image bytes are
  never stored — only attachment metadata (type + OCR confidence).
- **No OCR inside document PDFs.** The document pipeline (RAG) still extracts
  text runs only; a scanned/image-only PDF yields no text from the file. Paste
  a screenshot of the page instead to have it OCR'd via the image input.
- **PPTX: slide text runs only.** Speaker notes, tables rendered as images,
  and SmartArt text are not extracted.
- **Parser caps:** 25 MB per upload, 1000 PDF pages, 500 PPTX slides.
  Beyond that: split the file.
- **English-tuned retrieval** (stopword list); other languages work but
  rank noisier.

## Operations

- **No log rotation.** `logs\llama-server.log` and `logs\backend.log` grow
  until you delete them. On long-lived drives, clear `logs\` occasionally.
- **No watchdog.** If a process dies, nothing restarts it automatically.
  Re-running `START_AI.bat` is idempotent and recovers the stack.
- **No update mechanism.** Updating model/llama.cpp/dependencies is a
  manual builder step (`docs/INSTALLATION.md` §2.4).
- **Windows-only product.** Backend code is largely portable Python, but the
  launcher, core detection and stop scripts are `.bat`/PowerShell for
  Windows 10/11 x64.
- **Safe eject matters.** Run `STOP_AI.bat` before unplugging so SQLite WAL
  files flush. Pulling the drive mid-write can corrupt the databases
  (recoverable by deleting them; chat history is lost).
- **USB write cycles.** Chat and RAG writes go to the drive continuously.
  Normal USB flash endurance is fine for student-scale use; heavy daily use
  for years could wear cheap drives.

## Security (documented trade-offs, see `docs/SECURITY.md`)

- **Backend port 8090 has no authentication** — loopback-only by design;
  residual browser-attack surface (DNS rebinding) documented, not mitigated
  server-side.
- **Data at rest is unencrypted** — physical access to the drive exposes
  chat history and documents.
- **API key visible on the llama-server command line** — local-only defense
  in depth, not a secret.

## Scope

- **Single user, single conversation at a time.** No accounts, no multi-user
  isolation — out of scope for a personal USB tool.
- **No conversation export UI.** History lives in SQLite; export tooling is
  future work.
- **Analysis-first assistant.** No tool execution, no file editing, no
  agentic actions on the host — deliberately (security + RAM), and skills
  are prompts, not plugins with code.
- **Fixed model.** Swapping in another GGUF is possible but requires
  re-verifying architecture support, RAM fit and chat-template behavior
  (Qwen3.5-specific handling exists, e.g. thinking flags).
