"""Hardware profile selection and the runtime memory guard.

Profiles live in config/profiles/*.json (Phase 9):

- SAFE        unknown/constrained 8GB PCs: conservative budgets, one queued request
- NORMAL      clean systems: larger context budget when memory allows
- PERFORMANCE dev / high-memory machines: largest generation + history budgets

The guard re-checks free RAM before every inference so an 8GB machine is kept
out of swap (Phase 4 measured 4.6GB peak for the model server alone).
"""

from __future__ import annotations

import os

from backend.config.loader import HardwareProfile, PocketAIConfig
from backend.tools.sysinfo import MemoryInfo, get_memory


def select_profile(
    cfg: PocketAIConfig, mem: MemoryInfo | None = None
) -> tuple[str, HardwareProfile]:
    # The launcher (START_AI.bat) selects the profile BEFORE the model server
    # loads and exports it as POCKETAI_PROFILE. That is the correct moment to
    # measure: once the model is resident it consumes ~4.6GB, and re-measuring
    # would downgrade the profile unnecessarily. Unknown names are ignored so
    # a stale/typo'd env var can never break startup. An explicitly passed
    # mem always wins (deterministic selection for tests and tooling).
    if mem is None:
        forced = os.environ.get("POCKETAI_PROFILE", "").strip().lower()
        if forced in cfg.profiles:
            return forced, cfg.profiles[forced]
        mem = get_memory()

    sel = cfg.selection
    total_gb = mem.total_mb / 1024

    if mem.total_mb == 0 or total_gb < sel.force_safe_below_total_gb:
        return "safe", cfg.profiles["safe"]
    if total_gb <= sel.performance_above_total_gb:
        if mem.available_mb >= sel.normal_requires_free_mb_at_startup:
            return "normal", cfg.profiles["normal"]
        return "safe", cfg.profiles["safe"]
    perf = cfg.profiles.get("performance")
    if perf is not None and mem.available_mb >= sel.performance_requires_free_mb_at_startup:
        return "performance", perf
    return "normal", cfg.profiles["normal"]


def inference_allowed(
    cfg: PocketAIConfig,
    mem: MemoryInfo | None = None,
    profile: str | None = None,
) -> tuple[bool, int]:
    """Return (allowed, available_mb). Refuse when free RAM is too low.

    The memory guard is designed to prevent the system from running out of
    memory during inference, which would cause swapping or crashes.

    Key insight: The model is ALREADY loaded and consuming RAM by the time a
    request arrives (the supervisor keeps llama.cpp resident; Phase 4 measured
    ~4.6 GB peak for the server alone). So we never need to reserve enough RAM
    to load the model again. We only need headroom for the incremental cost of
    ONE request:
      - Backend process overhead (~100-200 MB)
      - Inference compute buffers (~200-400 MB)
      - KV cache growth for the active context window: ~150 MB at 4096 ctx
        (SAFE) up to ~300 MB at 8192 ctx (NORMAL)
      - OCR / document chunking if the request uses them (~100-200 MB)
    Total per-request increment: roughly 350 MB (SAFE) to ~700 MB (NORMAL).

    The threshold therefore has two factors:
      1. Total RAM — small machines must avoid swap, large machines have slack.
      2. Selected profile — NORMAL's larger context needs more KV headroom.

    Floors are chosen so that a healthy already-loaded system is never blocked,
    while genuinely starved memory (< floor) is refused before it thrashes:
      - 8 GB  -> 1200 MB (tight; protect against swap)
      - 16 GB -> 700 MB
      - 24 GB+-> 500 MB   (still far below the 3.7 GB free reported on a
                          24 GB machine, so that machine is correctly allowed)
    NORMAL profile adds +200 MB over the base to cover its larger KV cache.
    """
    mem = mem or get_memory()
    if mem.total_mb == 0:
        # Unknown platform: do not block inference, the OS will tell us.
        return True, mem.available_mb

    total_gb = mem.total_mb / 1024

    # Floor by total RAM class (same value used whether or not we know the
    # profile, so the guard is safe even before profile resolution).
    if total_gb <= 8:
        base_mb = cfg.selection.min_free_ram_mb_for_inference  # 1200
    elif total_gb <= 16:
        base_mb = max(700, cfg.selection.min_free_ram_mb_for_inference // 2)
    else:
        base_mb = max(500, cfg.selection.min_free_ram_mb_for_inference // 2 - 100)

    # The NORMAL profile runs the larger 8192-token context; its KV cache is
    # ~2x the SAFE 4096 window, so require a bit more headroom.
    profile_name = (profile or os.environ.get("POCKETAI_PROFILE", "")).strip().lower()
    if profile_name == "normal":
        required_mb = base_mb + 200
    else:
        required_mb = base_mb

    return mem.available_mb >= required_mb, mem.available_mb
