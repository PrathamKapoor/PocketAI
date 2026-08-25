# runtime/

Bundled, portable execution layer. Everything here runs from the USB drive —
no installation, no admin rights, drive-letter agnostic (all scripts derive
paths from `%~dp0`).

## Contents

| Item | What it is | Committed? |
|------|------------|------------|
| `llama.cpp/` | Official llama.cpp Windows CPU build (assembled at build time, see `docs/runtime_setup.md`) | No (binaries) |
| `python/` | Bundled Python 3.13 embeddable runtime + backend deps (built by `launcher\build_runtime.py`, see `docs/launcher_setup.md`) | No (binaries) |
| `start_model.bat` | Starts the model server on `127.0.0.1:8091` and waits until ready | Yes |
| `stop_model.bat` | Stops the PocketAI model server (matches by exe path, leaves other instances alone) | Yes |
| `detect_cores.ps1` | Physical/logical core detection used by `start_model.bat` | Yes |
| `benchmark.bat` | llama-bench throughput + server metrics (load time, RAM, CPU, tok/s) | Yes |
| `bench_server.ps1` | Server benchmark harness used by `benchmark.bat` | Yes |

## Usage

```bat
runtime\start_model.bat     :: start the model server
runtime\stop_model.bat      :: stop it
runtime\benchmark.bat       :: full benchmark, results in logs\
```

Settings (port, context, threads policy, API key) live in `config/model.json`;
the `.bat` scripts mirror those values for manual use. `start_model.bat`
honors `POCKETAI_CTX` when set (exported by `launcher\START_AI.bat` to apply
the selected hardware profile's server context; default 4096 otherwise).
