# PocketAI Performance

All numbers measured, not estimated. Sources: Phase 4 benchmark suite
(`runtime\benchmark.bat` → `logs\`), live smoke tests against the running
stack, and Phase 10 scenario testing.

---

## 1. Test machines

| | Dev machine (measurements below) | Target class |
|---|---|---|
| CPU | AMD Ryzen 7 7435HS, 8 cores / 16 threads, Zen 3+, AVX2 | Intel i5 11/12th-gen, 4–6 cores, AVX2 |
| RAM | 23.7 GB | 8 GB |
| GPU | none used (CPU-only build) | iGPU only (not used) |

Expect target machines to be **slower on tokens, much tighter on RAM**.

## 2. Inference (Qwen3.5-4B Q4_K_M, llama.cpp b10549, CPU)

Configuration: `-np 1`, `-fa on`, KV f16, batch 512, ctx 4096,
`-t 8 -tb 16` (physical / logical split).

| Metric | Result |
|---|---|
| Startup + model load (incl. warmup) | **6.5 s** |
| Generation (tg128, llama-bench) | **9.81 t/s** on physical cores (7.11 with SMT — the split matters) |
| Generation (live 128-tok runs) | **8.65 t/s avg** (8.68 / 9.08 / 8.18) |
| Prompt processing (pp512, llama-bench) | **48.73 t/s** with SMT (38.86 without) |
| Prompt processing (live 634-tok prompt) | **49.5 t/s** |
| RAM after load | 4,198 MB |
| **Peak RAM during inference** | **4,623 MB** |
| CPU during generation | 46–50 % of logical cores (= 8 physical cores fully busy) |
| Stability | 0 failed requests across all benchmark and smoke runs |

RAM breakdown: ≈ 2.7 GB weights + ≈ 1.9 GB compute buffers, KV cache and SSM
recurrent state.

### Live chat latency (dev machine, thinking off)

A representative "explain this TypeError" answer: 683 tokens in ~68 s
(~10 t/s end-to-end including queueing and HTTP). With thinking enabled the
same budget produced 1536 reasoning tokens in ~167 s and **no visible
answer** — why thinking defaults to off.

### Rule of thumb for users

~5–10 tokens/second on CPU. A solid paragraph ≈ 30–60 s. Prompt processing
is ~5× faster than generation, so long pasted context adds little latency.

## 3. Memory management (the 8 GB problem)

Windows needs ~2.5–3 GB; the model server peaks at 4.6 GB → under 1 GB
headroom on an 8 GB PC. PocketAI manages this explicitly:

| | SAFE | NORMAL | PERFORMANCE |
|---|---|---|---|
| Selected when | < 7 GB total, or tight | 7–12 GB, ≥ 4 GB free | > 12 GB, ≥ 6 GB free |
| max_generation_tokens | 768 | 1536 | 2048 |
| history_budget_tokens | 1200 | 2500 | 4000 |
| server context | 4096 | 8192 | 8192 |

Plus a **per-request guard**: `/chat` returns 503 when free RAM < 1200 MB.
Verified live in Phase 10 (8 GB simulation ran in `safe` mode; guard unit-
tested across the band edges). The design goal: degrade to a clear error,
never swap-thrash the machine.

## 4. RAG performance

Measured during Phase 10 scenario testing (dev machine):

| Operation | Input | Result |
|---|---|---|
| Large-PDF ingest | 600 pages, 4.83 MB | **1951 chunks in 15.1 s** (upload + extraction + chunking + indexing), backend healthy throughout |
| Search over 1951 chunks | keyword query | sub-second (BM25 linear scan) |
| Index rebuild after add/delete | whole corpus | well under 1 s at target corpus sizes |

Why this scales fine here:

- BM25 scoring is O(chunks × query tokens); at ~2 k chunks that is trivial.
  Even ~50 k chunks stays interactive on this hardware.
- The index rebuilds from SQLite only when the corpus changes.
- Uploads are capped (25 MB, 1000 PDF pages, 500 PPTX slides) so one file
  cannot dominate RAM or disk.

## 5. Startup and overhead

| Step | Time (dev machine) |
|---|---|
| Preflight (all checks) | < 1 s |
| llama-server load + warmup | ~6.5 s |
| Backend start + health ready | ~2 s |
| Browser open | immediately after health |
| **Total plug-to-chat** | **~10 s** |

Backend overhead per request is negligible (milliseconds) against multi-
second generation; requests are queued through a semaphore because the
server runs one slot.

## 6. Threading evidence (why `-t` physical / `-tb` logical)

llama-bench, 2 reps, same build/model:

| Threads | pp512 (t/s) | tg128 (t/s) |
|---|---|---|
| 8 (physical) | 38.86 ± 0.08 | **9.81 ± 0.26** |
| 16 (logical) | **48.73 ± 0.73** | 7.11 ± 0.33 |

Generation — the interactive experience — is 38 % faster on physical cores;
prompt processing is 25 % faster with SMT. `start_model.bat` detects core
counts (`detect_cores.ps1`) and applies the split automatically.

## 7. What was deliberately NOT optimized

- **Parallel requests** — one slot (`-np 1`). More slots multiply KV-cache
  RAM on 8 GB machines for zero benefit to a single user.
- **Embedding-based retrieval** — a second model process doesn't fit;
  BM25 is the RAM-aware choice (seam kept for the future).
- **GPU paths** — targets are iGPU-only; the CPU build is the product.
