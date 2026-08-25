"""Memory protection tests: profile selection + the per-request guard."""

from __future__ import annotations

from pathlib import Path

from backend.config.loader import load_config
from backend.supervisor import router as router_module
from backend.tools.hardware import inference_allowed, select_profile
from backend.tools.sysinfo import MemoryInfo

ROOT = Path(__file__).resolve().parents[2]


def _mb(gb: float) -> int:
    return int(gb * 1024)


def test_profile_selection_rules():
    cfg = load_config(ROOT)
    # Unknown machine (0 reported) -> SAFE.
    assert select_profile(cfg, MemoryInfo(0, 0))[0] == "safe"
    # Small machine -> always SAFE.
    assert select_profile(cfg, MemoryInfo(_mb(4), _mb(3)))[0] == "safe"
    # 8GB machine, little free RAM -> SAFE.
    assert select_profile(cfg, MemoryInfo(_mb(8), _mb(2)))[0] == "safe"
    # 8GB machine, clean (enough free at startup) -> NORMAL.
    assert select_profile(cfg, MemoryInfo(_mb(8), _mb(5)))[0] == "normal"
    # Large machine but almost no free RAM -> NORMAL (performance bar not met).
    assert select_profile(cfg, MemoryInfo(_mb(24), _mb(1)))[0] == "normal"
    # Large machine with ample free RAM -> PERFORMANCE.
    assert select_profile(cfg, MemoryInfo(_mb(24), _mb(12)))[0] == "performance"


def test_performance_profile_requires_free_ram():
    cfg = load_config(ROOT)
    bar = cfg.selection.performance_requires_free_mb_at_startup
    # Just below the free-RAM bar -> falls back to NORMAL, not SAFE.
    assert select_profile(cfg, MemoryInfo(_mb(16), bar - 1))[0] == "normal"
    assert select_profile(cfg, MemoryInfo(_mb(16), bar))[0] == "performance"


def test_missing_performance_profile_falls_back_to_normal():
    cfg = load_config(ROOT)
    cfg.profiles.pop("performance", None)
    assert select_profile(cfg, MemoryInfo(_mb(24), _mb(12)))[0] == "normal"


def test_launcher_env_override(monkeypatch):
    cfg = load_config(ROOT)
    monkeypatch.setenv("POCKETAI_PROFILE", "safe")
    # No mem passed: the launcher's exported decision wins.
    assert select_profile(cfg)[0] == "safe"
    # Unknown / mistyped values are ignored, not trusted.
    monkeypatch.setenv("POCKETAI_PROFILE", "turbo")
    assert select_profile(cfg)[0] != "turbo"
    # An explicit mem measurement still wins over the env var.
    monkeypatch.setenv("POCKETAI_PROFILE", "performance")
    assert select_profile(cfg, MemoryInfo(_mb(4), _mb(3)))[0] == "safe"


def test_inference_guard():
    cfg = load_config(ROOT)
    threshold = cfg.selection.min_free_ram_mb_for_inference
    allowed, _ = inference_allowed(cfg, MemoryInfo(_mb(8), threshold - 1))
    assert allowed is False
    allowed, _ = inference_allowed(cfg, MemoryInfo(_mb(8), threshold + 1))
    assert allowed is True
    # Unknown platform (0 total) must not block inference.
    allowed, _ = inference_allowed(cfg, MemoryInfo(0, 0))
    assert allowed is True


def test_guard_blocks_chat_with_503(client, mock_llama, monkeypatch):
    monkeypatch.setattr(
        router_module, "get_memory", lambda: MemoryInfo(_mb(8), 100)
    )
    resp = client.post(
        "/chat", json={"message": "Explain this traceback in detail please"}
    )
    assert resp.status_code == 503
    assert "memory" in resp.json()["error"].lower()
    assert mock_llama.chat_calls == 0


def test_inference_guard_scales_with_total_ram():
    """Memory guard should be more lenient on machines with more total RAM."""
    cfg = load_config(ROOT)
    threshold = cfg.selection.min_free_ram_mb_for_inference

    # 8GB machine: conservative threshold (uses min_free_ram_mb_for_inference)
    allowed_8gb, _ = inference_allowed(cfg, MemoryInfo(_mb(8), threshold))
    assert allowed_8gb is True

    # 16GB machine: moderate threshold (~700 MB). 700 MB free is allowed,
    # 600 MB free is blocked.
    allowed_16gb, _ = inference_allowed(cfg, MemoryInfo(_mb(16), 700))
    assert allowed_16gb is True
    blocked_16gb, _ = inference_allowed(cfg, MemoryInfo(_mb(16), 600))
    assert blocked_16gb is False

    # 24GB machine: lenient threshold (~500 MB). 500 MB free is allowed,
    # 400 MB free is blocked.
    allowed_24gb, _ = inference_allowed(cfg, MemoryInfo(_mb(24), 500))
    assert allowed_24gb is True
    blocked_24gb, _ = inference_allowed(cfg, MemoryInfo(_mb(24), 400))
    assert blocked_24gb is False

    # 8GB machine with very low free RAM should be blocked
    blocked_8gb, _ = inference_allowed(cfg, MemoryInfo(_mb(8), 100))
    assert blocked_8gb is False


def test_inference_guard_normal_profile_needs_more_headroom():
    """NORMAL runs an 8192-token context (2x SAFE KV cache) so requires +200 MB."""
    cfg = load_config(ROOT)
    # At exactly the 24GB floor (500 MB) a non-NORMAL machine is allowed, but a
    # NORMAL machine needs 700 MB, so 500 MB is blocked.
    allowed_plain, _ = inference_allowed(cfg, MemoryInfo(_mb(24), 500))
    assert allowed_plain is True
    blocked_normal, _ = inference_allowed(
        cfg, MemoryInfo(_mb(24), 500), profile="normal"
    )
    assert blocked_normal is False
    allowed_normal, _ = inference_allowed(
        cfg, MemoryInfo(_mb(24), 700), profile="normal"
    )
    assert allowed_normal is True


def test_inference_guard_realistic_24gb_machine():
    """A 24GB machine with 3.7GB free should NOT be blocked."""
    cfg = load_config(ROOT)
    # 24GB total, 3.7GB (3700 MB) free - should be allowed
    allowed, _ = inference_allowed(cfg, MemoryInfo(_mb(24), 3700))
    assert allowed is True
