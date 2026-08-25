# PocketAI Runtime Setup (Phase 4)

Verified portable inference runtime: llama.cpp + Qwen3.5-4B Q4_K_M, CPU-only,
no installation, drive-letter agnostic. All measurements below were taken on
the dev machine before any backend code was written.

---

## 1. llama.cpp runtime

| | |
|---|---|
| Version | `0.1.2-dev`, **build 10549**, commit `b2e5e9b28` |
| Built with | Clang 20.1.8 for Windows x86_64 |
| Source | Official release assets from <https://github.com/ggml-org/llama.cpp/releases> — the `win-cpu-x64` zip (re-verify against the build ID above when updating) |
| Location | `runtime/llama.cpp/` |
| Key binaries | `llama-server.exe` (OpenAI-compatible server), `llama-cli.exe`, `llama-bench.exe` |
| Dependencies | None — all DLLs are bundled (`ggml*.dll`, `llama*.dll`, `libomp.dll`). No installer, no admin rights, no CUDA/Vulkan/Metal: pure CPU build |

CPU backend is selected automatically at startup by the ggml dispatcher
(e.g. `ggml-cpu-haswell.dll` = AVX2 on CPUs without AVX-512). Verified on
AMD Ryzen 7 7435HS (Zen 3+, AVX2) — the same ISA class as the target
i5 11th/12th-gen college PCs, so benchmarks are representative.

## 2. Model

| | |
|---|---|
| File | `models/Qwen3.5-4B-Q4_K_M.gguf` |
| Architecture | `qwen35` — hybrid SSM + attention (`full_attention_interval = 4`: 3 recurrent SSM layers per 1 full-attention layer) |
| Parameters | 4.21 B |
| Quantization | Q4_K_M |
| Size | 2,740,937,888 bytes (2.54 GiB) |

### qwen35 support validation (build 10549)

- Model loads correctly; architecture auto-detected as `qwen35`.
- Hybrid SSM + attention graph executes without errors; compute-graph reuse
  works (`graphs reused` in server log).
- **No unsupported operations, no crashes** across all benchmark and API runs.
- **No special flags required** for qwen35 — standard llama.cpp flags work.
  Flash Attention (`-fa on`) is supported and validated for this architecture
  on CPU.
- Qwen3.5 is a *thinking* model: responses start in `reasoning_content`
  before producing visible content. The backend (Phase 5) must budget
  thinking tokens (see `--reasoning-format`).

## 3. Configuration

Single source of truth: `config/model.json`. The `.bat` launchers mirror its
values for manual use.

| Setting | Value | Why |
|---|---|---|
| context_size | 4096 default | Balanced for RAG chunks + chat on 8 GB machines; the launcher overrides it per hardware profile (see §4) |
| threads | auto → physical cores | SMT hurts token generation (measured) |
| threads_batch | auto → logical cores | SMT helps prompt processing (measured) |
| batch_size / ubatch | 512 / 512 | Phase 4 spec; matches pp benchmark |
| n_gpu_layers | 0 | CPU-only target (iGPU, no CUDA) |
| flash_attention | on (`-fa on`) | Supported for qwen35 CPU; reduces KV memory |
| kv_cache_type | f16 | Default; validated |
| load_mode | auto (mmap) | Weights page-evictable under RAM pressure |
| parallel slots | 1 (`-np 1`) | Single-user assistant; default auto=4 wasted RAM |
| host / port | 127.0.0.1 / **8091** | 8080 conflicted with Oracle TNS Listener on a dev PC and is the most common squatted port; 8091 also avoids Ollama (11434) and LM Studio (1234) |
| api_key | local key in config | Blocks other local processes / web pages from using the model API (defense in depth; not a real secret) |

## 4. Launch

```bat
runtime\start_model.bat     :: detect cores, start server, wait until ready
runtime\stop_model.bat      :: stop the PocketAI server (path-matched; other
                             ::    llama.cpp instances are left alone)
runtime\benchmark.bat       :: llama-bench + server metrics -> logs\
```

All scripts derive paths from `%~dp0` — they work from any drive letter.
PowerShell is invoked via its absolute path
(`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`) so a stripped
`PATH` on a locked-down PC cannot break them.

Phase 9: when started via `launcher\START_AI.bat`, the launcher exports
`POCKETAI_CTX` (the selected hardware profile's `recommended_server_context`);
`start_model.bat` uses it for `-c` when defined, else falls back to 4096.

Equivalent manual command (from the PocketAI root):

```bat
runtime\llama.cpp\llama-server.exe ^
  -m models\Qwen3.5-4B-Q4_K_M.gguf ^
  --host 127.0.0.1 --port 8091 ^
  -c 4096 -t 8 -tb 16 -b 512 -ub 512 ^
  -ngl 0 -fa on -ctk f16 -ctv f16 -np 1 ^
  -a qwen3.5-4b --jinja --api-key <key from config\model.json> ^
  --log-file logs\llama-server.log
```

API endpoints (all require `Authorization: Bearer <key>` except `/health`):

- `GET /health` — readiness probe (200 = model loaded)
- `GET /v1/models` — lists alias `qwen3.5-4b`
- `POST /v1/chat/completions` — OpenAI-compatible chat

## 5. Optimization choices (evidence-based)

1. **Thread split `-t <physical> -tb <logical>`** — llama-bench, 2 reps:

   | threads | pp512 (t/s) | tg128 (t/s) |
   |---|---|---|
   | 8 (physical) | 38.86 ± 0.08 | **9.81 ± 0.26** |
   | 16 (logical) | **48.73 ± 0.73** | 7.11 ± 0.33 |

   Generation (the interactive experience) is 38 % faster on physical cores;
   prompt processing is 25 % faster with SMT. The split gives both.

2. **`-np 1`** — server default created 4 slots × 4096 ctx; single-user
   PocketAI needs one. Saves RAM at load and avoids slot contention.

3. **`-fa on`** — validated for qwen35 on CPU in this build (fa column = 1 in
   llama-bench output).

4. **Warmup kept enabled** (default) — first-request latency is paid at
   startup instead of on the user's first message.

## 6. Benchmark results

Dev machine: AMD Ryzen 7 7435HS (8 cores / 16 threads, Zen 3+, AVX2),
23.7 GB RAM, Windows. Target machines (i5 11/12th-gen, 8 GB) will be slower
and much more RAM-constrained.

`runtime\benchmark.bat`, ctx 4096, batch 512, KV f16, FA on, `-np 1`:

| Metric | Result |
|---|---|
| Startup + model load (incl. warmup) | **6.5 s** |
| Generation speed (128 tok × 3 runs) | **8.65 t/s avg** (8.68 / 9.08 / 8.18) |
| Prompt processing (634-tok prompt) | **49.5 t/s** |
| RAM after load | 4,198 MB |
| Peak RAM during inference | **4,623 MB** |
| CPU utilization during generation | 46–50 % of logical cores (= the 8 physical cores fully busy) |
| Stability | **0 failed requests** across all runs; no crashes, no unsupported-op warnings |

RAM breakdown: ≈2.7 GB weights + ≈1.9 GB compute buffers, KV cache and SSM
recurrent state.

### 8 GB target implications

Peak 4.6 GB for the server + ≈2.5–3 GB Windows leaves under ~1 GB headroom on
an 8 GB PC. Workable, but tight — see the Phase 4 architecture review in the
completion report. Mitigations if target testing shows swapping: close the
browser during heavy generation, drop ctx to 2048 via a hardware profile, or
ship a smaller-quant fallback profile.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `couldn't bind HTTP server socket` | Port already in use. Change `server.port` in `config\model.json` and the `PORT` var in `start_model.bat`. |
| `401` on API calls | Missing/wrong `Authorization: Bearer <key>` header. Key lives in `config\model.json`. |
| Slow generation | Check `logs\llama-server.log` threadpool line; ensure `-t` = physical cores and the CPU supports AVX2. |
| High RAM pressure on 8 GB | See §6 mitigations; consider a lower-ctx hardware profile. |
| Server exits at startup | Read `logs\llama-server.log`; if empty, run `llama-server.exe` manually in a console to see stderr. |
