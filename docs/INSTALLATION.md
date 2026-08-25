# PocketAI Installation

There is nothing to install — that is the point. This document covers two
audiences: **users** who received a prepared PocketAI drive, and **builders**
who assemble one from source.

---

## 1. For users (prepared drive)

Requirements: Windows 10/11 x64 PC, ~8 GB RAM minimum, a free USB port.
No internet, no admin rights, no prior software needed.

1. Plug in the USB drive (any drive letter works — E:, G:, whatever Windows
   assigns).
2. Open the drive and double-click **`launcher\START_AI.bat`**.
3. Wait. Preflight checks the machine, the model server loads (~7 s on a
   modern CPU, longer on older ones), the backend starts, and your browser
   opens at `http://127.0.0.1:8090/`.
4. Done. See `docs/USER_GUIDE.md` for using it.

To stop: double-click **`launcher\STOP_AI.bat`** (or close the two minimized
console windows). Run STOP_AI before ejecting the USB drive so SQLite can
flush its write-ahead log.

### Moving the drive between PCs

Just copy. Nothing records absolute paths:

- Verified: full copy to a fresh folder boots on first try (own database,
  own logs).
- Verified: drive-letter change (E: → G: via `subst`) — full start/stop cycle
  works unchanged.

Chat history and uploaded documents travel **with** the drive
(`storage/pocket_ai.db`, `rag/`). If you want a clean slate on a new machine,
delete those two and `rag/uploads/` while the stack is stopped.

### First-run requirements check

`preflight.py` runs automatically and fails with a readable reason if:

| Check | Failure message pattern |
|---|---|
| Python ≥ 3.10 (bundled or system) | `Python too old` / `no usable Python found` |
| Required modules (pydantic, fastapi, uvicorn, httpx, pypdf) | `missing Python modules` |
| `runtime\llama.cpp\llama-server.exe` present | `missing llama-server` |
| Model file from `config\model.json` present | `missing model file: models\…` + hint |
| `backend\main.py`, `frontend\index.html` present | `missing backend/frontend` |
| Ports 8090/8091 | `port 8090 (backend) is already used by another program` + hints |

Preflight results are written to `logs\preflight.env` for inspection.

## 2. For builders (assembling a drive from source)

Build once on any Windows dev machine with internet; the result runs offline.

### 2.1 Prerequisites

- Windows x64, internet access (build time only).
- ~6 GB free disk for the bundle.
- Host Python 3.10+ (for `build_runtime.py` and tests).

### 2.2 Steps

1. **Clone/copy the repository** (scripts, backend, frontend, skills, config
   are all in git; binaries are git-ignored).

2. **llama.cpp** — download the official `win-cpu-x64` release zip
   (verified: build **b10549**) from
   `https://github.com/ggml-org/llama.cpp/releases` and extract it to
   `runtime\llama.cpp\` so that `runtime\llama.cpp\llama-server.exe` exists.
   Re-verify new builds against the model before shipping (see
   `docs/runtime_setup.md` §2 for what was validated).

3. **Model** — place `Qwen3.5-4B-Q4_K_M.gguf` (2.54 GiB) in `models\`. The
   filename must match `file` in `config\model.json`.

4. **Bundled Python** — run:

   ```bat
   python launcher\build_runtime.py
   ```

   Downloads the official Python 3.13 embeddable package into
   `runtime\python\`, enables `import site`, and pip-installs the pinned
   requirements (`fastapi`, `uvicorn`, `httpx`, `pydantic`, `pypdf`) **into
   the bundle**. Run it with `PYTHONNOUSERSITE=1` set, otherwise your host
   user-site packages can silently leak into the bundle and break on clean
   machines.

5. **Verify** — from the PocketAI root:

   ```bat
   python -m pytest backend/tests -q        :: expect 78 passed, 1 skipped
   launcher\START_AI.bat                    :: full stack + browser
   ```

### 2.3 What belongs on the USB drive (final layout)

```
pocket_ai/
├── launcher/       START_AI.bat, STOP_AI.bat, preflight.py, …
├── runtime/        llama.cpp/ (binaries + DLLs), python/ (bundled), *.bat/*.ps1
├── models/         Qwen3.5-4B-Q4_K_M.gguf
├── backend/        FastAPI app + tests
├── frontend/       index.html, app.js, style.css
├── rag/            pipeline code (uploads/ and vector_store/ data grow here)
├── skills/         15 skill folders
├── config/         *.json + profiles/
├── storage/        (created at runtime)
├── logs/           (created at runtime)
└── docs/           this documentation
```

Total size ≈ 3 GB (model 2.54 GiB + llama.cpp ~100 MB + Python bundle
~120 MB + code).

### 2.4 Updating components

| Component | How |
|---|---|
| Skills | Add/edit `skills/<id>/skill.md`, restart the backend |
| Profiles | Edit `config/profiles/*.json`, restart the stack |
| Backend/frontend code | Replace files, restart the stack |
| llama.cpp | Swap `runtime/llama.cpp/`, re-run benchmarks, restart |
| Bundled Python deps | Re-run `launcher\build_runtime.py` |

## 3. Uninstalling

Delete the folder. Stop the stack first (`STOP_AI.bat`). Nothing is written
outside the PocketAI root at runtime — no registry keys, no AppData files,
no services. (Build-time only: `build_runtime.py` uses the pip cache and a
temp directory, both standard.)
