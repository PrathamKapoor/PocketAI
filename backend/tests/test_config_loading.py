"""Configuration loading tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config.loader import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[2]


def test_load_config():
    cfg = load_config(ROOT)
    assert cfg.model_server.host == "127.0.0.1"
    assert cfg.model_server.port == 8091
    assert cfg.model_server.alias == "qwen3.5-4b"
    assert cfg.model_server.api_key
    assert cfg.backend.host == "127.0.0.1"
    assert cfg.backend.port == 8090


def test_profiles_defined():
    cfg = load_config(ROOT)
    assert set(cfg.profiles) == {"safe", "normal", "performance"}
    for profile in cfg.profiles.values():
        assert profile.max_generation_tokens > 0
        assert profile.history_budget_tokens > 0
        assert profile.parallel_requests == 1  # server runs -np 1
        assert profile.recommended_server_context >= 4096
    assert cfg.selection.min_free_ram_mb_for_inference > 0
    # Budgets must be ordered: SAFE < NORMAL < PERFORMANCE.
    assert (
        cfg.profiles["safe"].max_generation_tokens
        < cfg.profiles["normal"].max_generation_tokens
        < cfg.profiles["performance"].max_generation_tokens
    )


def test_paths_resolved_from_root():
    cfg = load_config(ROOT)
    assert cfg.skills_dir == ROOT / "skills"
    assert cfg.database_path == ROOT / "storage" / "pocket_ai.db"
    assert cfg.require_loopback_bind is True
    # Internal diagnostics stay hidden unless explicitly enabled.
    assert cfg.developer_mode is False


def test_missing_config_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_invalid_json_raises(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "model.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def _raw_configs():
    """Load the real config JSON so profile resolution works, for tampering."""
    import json

    def _read(name):
        return json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))

    return _read("model.json"), _read("hardware.json"), _read("runtime.json")


def test_offbox_model_server_rejected_when_loopback_required():
    from backend.config.loader import PocketAIConfig

    model_json, hardware_json, runtime_json = _raw_configs()
    model_json["server"]["host"] = "8.8.8.8"
    runtime_json["security"]["require_loopback_bind"] = True
    with pytest.raises(ConfigError):
        PocketAIConfig(ROOT, model_json, hardware_json, runtime_json)


def test_offbox_model_server_allowed_when_loopback_disabled():
    from backend.config.loader import PocketAIConfig

    model_json, hardware_json, runtime_json = _raw_configs()
    model_json["server"]["host"] = "10.0.0.5"
    runtime_json["security"]["require_loopback_bind"] = False
    cfg = PocketAIConfig(ROOT, model_json, hardware_json, runtime_json)
    assert cfg.model_server.host == "10.0.0.5"
