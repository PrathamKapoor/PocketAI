# PocketAI Launcher & Portable Packaging (Phase 9)

Verified one-click launcher: `launcher\START_AI.bat` detects the hardware,
selects a profile, starts the model server and the backend, and opens the
browser — from any drive letter, with no installation and no internet.
Everything below was tested live on the dev machine (Windows, 24 GB RAM).

```
Double-click START_AI.bat
  → resolve Python (bundled runtime\python first)
  → pre-flight checks (deps, config, files, RAM, ports)
  → select profile: SAFE / NORMAL / PERFORMANCE
  → start llama-server (:8091, ctx from profile)
  → start FastAPI backend (:8090)
  → open http://127.0.0.1:8090/
```

---

## 1. Components

```
launcher/
├── START_AI.bat       # One-click start (the entry point for target users)
├── STOP_AI.bat        # Stops backend + model server (path-matched)
├── preflight.py       # Startup checks + profile decision (stdlib-only)
├── stop_backend.ps1   # Backend process matcher used by STOP_AI.bat
├── build_runtime.py   # Builds the bundled runtime\python (needs internet ONCE)
└── install_deps.bat   # Fallback: pip-install deps into a system Python
```

All scripts derive paths from `%~dp0` / `Path(__file__)` — the USB drive
letter (E:, F:, G:, …) is never assumed. PowerShell is invoked via its
absolute path (`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`)
so a stripped `PATH` on a locked-down PC cannot break them.

## 2. Pre-flight checks

`preflight.py` runs BEFORE anything is started and aborts with a specific,
actionable message instead of a half-started stack. Checks, in order:

| # | Check | Failure message points at |
|---|---|---|
| 1 | Python ≥ 3.10 | restore `runtime\python` or install Python 3.10+ |
| 2 | Backend deps importable (`pydantic`, `fastapi`, `uvicorn`, `httpx`, `pypdf`) | run `launcher\install_deps.bat` or restore `runtime\python` |
| 3 | Config loads (`config/*.json` + `config/profiles/*.json`) | check the `config\` folder is complete |
| 4 | Required files exist: `runtime\llama.cpp\llama-server.exe`, the model `.gguf` from `config/model.json`, `backend\main.py`, `frontend\index.html` | re-copy the full PocketAI folder |
| 5 | RAM measured → profile selected | (never fails; unknown platform → SAFE) |
| 6 | Ports 8091 (model) and 8090 (backend): `free`, `ours`, or `busy` | busy → close the other program / run STOP_AI.bat / change the port |

Port detection distinguishes three states: a healthy PocketAI service
(`ours` — an already-running stack is reused, so START_AI.bat is idempotent),
a free port, and a foreign process (`busy` — startup refused).

Results are written machine-readable to `logs\preflight.env`:

```
OK=1  PROFILE=performance  CTX=8192  TOTAL_MB=24260  FREE_MB=7556
BACKEND_HOST=127.0.0.1  BACKEND_PORT=8090  MODEL_PORT=8091
MODEL_READY=0  BACKEND_READY=0
```

`preflight.py` is stdlib-only on purpose: it must produce a useful error even
when the backend dependencies are missing.

## 3. Hardware profiles

Profiles live in `config/profiles/*.json` (file name stem = profile id):

| | SAFE | NORMAL | PERFORMANCE |
|---|---|---|---|
| For | Unknown / constrained 8 GB PCs (college) | Clean 8 GB+ systems | Dev / high-memory machines |
| max_generation_tokens | 768 | 1536 | 2048 |
| history_budget_tokens | 1200 | 2500 | 4000 |
| recommended_server_context | 4096 | 8192 | 8192 |

Selection (rules in `config/hardware.json`, code in
`backend/tools/hardware.py`):

- total RAM < 7 GB → `safe`
- 7–12 GB → `normal` if ≥ 4000 MB free at startup, else `safe`
- \> 12 GB → `performance` if ≥ 6000 MB free and the profile exists, else `normal`
- unknown platform (RAM unreadable) → `safe`

### Why the launcher decides, not the backend

The profile must be chosen BEFORE the model server loads: the server consumes
~4.6 GB once resident, so a backend that measures RAM at its own startup
would downgrade the profile on every cold start. `START_AI.bat` therefore
exports the pre-flight decision as `POCKETAI_PROFILE`, and the backend's
`select_profile()` honors it (unknown values are ignored; an explicit RAM
measurement always wins, which keeps tests deterministic). The profile's
`recommended_server_context` is passed to `runtime\start_model.bat` the same
way via `POCKETAI_CTX`.

The per-request RAM guard (503 when free RAM < 1200 MB) still applies on
every `/chat` request regardless of profile.

## 4. The bundled Python runtime

`runtime\python` is the official CPython **embeddable package** (3.13.14,
amd64) with `site-packages` enabled, pip bootstrapped, and
`backend\requirements.txt` installed into it. The target machine needs
neither Python nor internet.

Build it once on any machine with internet (from the PocketAI root):

```bat
python launcher\build_runtime.py            :: build
python launcher\build_runtime.py --force    :: rebuild over an existing runtime\python
```

The build downloads from python.org and bootstrap.pypa.io, then verifies that
every dependency actually loads from `runtime\python` (not from some other
Python on the build machine — pip silently "satisfies" requirements from the
host environment otherwise; the build sets `PYTHONNOUSERSITE=1` and asserts
each module's `__file__` to prevent that).

`runtime\python` is git-ignored (binaries); it ships on the USB drive.

### Fallback: system Python

If `runtime\python` is missing, `START_AI.bat` falls back to `python` /
`py` on PATH. Run `launcher\install_deps.bat` once on such a machine to
install `backend\requirements.txt` into it.

## 5. Stopping

`STOP_AI.bat` stops both processes and nothing else:

- **Backend** — matched via `Win32_Process` CommandLine containing
  `backend\main.py` AND this PocketAI root (case-insensitive), so a backend
  from another copy/folder is left alone.
- **Model server** — matched by the `runtime\llama.cpp\llama-server.exe`
  path prefix (same rule as `runtime\stop_model.bat`); other llama.cpp
  instances are untouched.

## 6. Verified behavior (dev machine)

| Scenario | Result |
|---|---|
| Cold start via `START_AI.bat` | preflight → PERFORMANCE (ctx=8192) → model server ready → backend ready → browser opened; `/health` fully `ok` |
| `/system` after start | `profile: performance`, `max_generation_tokens: 2048` — matches the launcher's decision (env override works) |
| `START_AI.bat` while already running | preflight reports both ports "already running", nothing is restarted |
| Port 8090 held by a foreign process | `PREFLIGHT FAILED: port 8090 (backend) is already used by another program` + fix hints; `OK=0` in `preflight.env`; nothing started |
| `STOP_AI.bat` | backend and model server PIDs found by path match and stopped; both ports verified free afterwards |

## 7. Final USB layout

```
POCKET_AI/                  (copy the whole folder to the USB drive)
├── launcher\START_AI.bat   ← double-click this
├── launcher\STOP_AI.bat
├── runtime\                llama.cpp build + bundled Python + start/stop scripts
├── models\Qwen3.5-4B-Q4_K_M.gguf
├── backend\                FastAPI app
├── frontend\               browser UI (served by the backend)
├── rag\                    retrieval pipeline (uploads/work are runtime data)
├── skills\                 12 skill files
├── tools\                  dev tooling
├── config\                 model/hardware/runtime JSON + profiles\
├── docs\                   this documentation
├── storage\                SQLite (created on first run)
└── logs\                   created on first run
```

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No Python runtime found` | `runtime\python` missing and no system Python. Restore the folder or install Python 3.10+ and run `launcher\install_deps.bat` |
| `missing Python packages: …` | Deps not installed for the Python in use. Run `launcher\install_deps.bat` (system Python) or rebuild `runtime\python` |
| `missing model file: models\…gguf` | The 2.5 GB model was not copied to the drive (it is git-ignored). Copy it from the source machine |
| `port … is already used by another program` | Follow the printed hints; `STOP_AI.bat` if a stale PocketAI stack is suspected |
| Backend started but profile looks wrong | Check `POCKETAI_PROFILE` is exported by `START_AI.bat`; the backend honors it only when it has not measured RAM itself |
| Anything else | Read `logs\preflight.env`, `logs\backend.log`, `logs\llama-server.log` |
