"""Configuration loader.

Reads the three source-of-truth files under <root>/config:

- model.json     llama.cpp model + server connection (Phase 4)
- hardware.json  profile selection rules; profiles live in config/profiles/
                 (safe / normal / performance) as of Phase 9
- runtime.json   backend server, paths, chat limits, security switches

Nothing is hardcoded downstream: every path/limit comes from here.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class ConfigError(RuntimeError):
    """Raised when a config file is missing or invalid."""


# Hosts that are guaranteed to stay on the local machine. PocketAI refuses to
# talk to (or bind) anything else so prompts/keys can never leave the device.
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ModelServer(BaseModel):
    model_config = ConfigDict(extra="allow")

    host: str
    port: int
    alias: str
    api_key: str
    timeout_seconds: int = 600


class BackendServer(BaseModel):
    model_config = ConfigDict(extra="allow")

    host: str = "127.0.0.1"
    port: int = 8090
    log_level: str = "info"


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    skills_dir: str = "skills"
    storage_dir: str = "storage"
    database_file: str = "storage/pocket_ai.db"
    logs_dir: str = "logs"
    rag_uploads_dir: str = "rag/uploads"
    rag_database_file: str = "rag/vector_store/documents.db"


class ChatLimits(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_message_chars: int = 8000
    max_history_messages: int = 20
    chars_per_token_estimate: int = 4
    enable_thinking: bool = False


class RagLimits(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_upload_mb: int = 25
    chunk_chars: int = 1200
    chunk_overlap: int = 150
    search_top_k: int = 6
    chat_top_k: int = 3
    chat_context_max_chars: int = 4000
    allowed_extensions: list[str] = [".pdf", ".txt", ".md", ".markdown", ".pptx"]


class HardwareProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    description: str = ""
    max_generation_tokens: int
    history_budget_tokens: int
    parallel_requests: int = 1
    recommended_server_context: int = 4096

class HardwareSelection(BaseModel):
    model_config = ConfigDict(extra="allow")

    force_safe_below_total_gb: float = 7.0
    normal_requires_free_mb_at_startup: int = 4000
    performance_above_total_gb: float = 12.0
    performance_requires_free_mb_at_startup: int = 6000
    min_free_ram_mb_for_inference: int = 1200


class ImageLimits(BaseModel):
    """Image-input pipeline limits + bundled OCR engine locations.

    Paths are resolved relative to the PocketAI root so the USB drive letter
    can change. OCR is fully offline: the Tesseract binary and its language
    data ship inside runtime/ocr and are never downloaded at runtime.
    """

    model_config = ConfigDict(extra="allow")

    max_upload_mb: int = 10
    # Images larger than this (on the longest side) are downscaled before OCR
    # to bound both memory and OCR time on an 8 GB target.
    max_dimension: int = 2400
    min_dimension: int = 8
    allowed_extensions: list[str] = [".png", ".jpg", ".jpeg", ".webp", ".bmp"]
    allowed_mime: list[str] = [
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/bmp",
        "image/x-ms-bmp",
    ]
    tesseract_relative: str = "runtime/ocr/tesseract.exe"
    tessdata_relative: str = "runtime/ocr/tessdata"
    temp_dir_relative: str = "logs/image_tmp"
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    # Pillow decompression-bomb guard (pixels). Beyond this the image is
    # rejected rather than decoded into RAM.
    max_pixels: int = 100_000_000


class PocketAIConfig:
    """Typed view over the three JSON config files."""

    def __init__(
        self,
        root: Path,
        model_json: dict,
        hardware_json: dict,
        runtime_json: dict,
    ) -> None:
        self.root = Path(root)

        try:
            self.model_server = ModelServer(**model_json["server"])
        except Exception as exc:  # pydantic ValidationError or KeyError
            raise ConfigError(f"model.json: invalid 'server' section: {exc}") from exc
        self.model_info = model_json.get("model", {})

        try:
            self.backend = BackendServer(**runtime_json.get("backend", {}))
            self.paths = PathsConfig(**runtime_json.get("paths", {}))
            self.chat = ChatLimits(**runtime_json.get("chat", {}))
            self.rag = RagLimits(**runtime_json.get("rag", {}))
        except Exception as exc:
            raise ConfigError(f"runtime.json invalid: {exc}") from exc
        self.require_loopback_bind = bool(
            runtime_json.get("security", {}).get("require_loopback_bind", True)
        )
        # Never let a tampered/off-box model server host exfiltrate prompts or
        # the local API key. Mirrors the backend bind guard (F-1).
        if self.require_loopback_bind and self.model_server.host not in LOOPBACK_HOSTS:
            raise ConfigError(
                f"security.require_loopback_bind is enabled but model.json points "
                f"the model server at non-loopback host {self.model_server.host!r}. "
                f"PocketAI must stay on localhost; refusing to start."
            )
        # Developer mode exposes internal diagnostics (GET /skills). It is
        # off by default so normal users never see internal architecture.
        self.developer_mode = bool(
            runtime_json.get("security", {}).get("developer_mode", False)
        )

        try:
            self.selection = HardwareSelection(**hardware_json.get("selection", {}))
        except Exception as exc:
            raise ConfigError(f"hardware.json invalid: {exc}") from exc
        self.profiles = self._load_profiles(hardware_json)
        if "safe" not in self.profiles or "normal" not in self.profiles:
            raise ConfigError(
                "config must define 'safe' and 'normal' profiles "
                "(config/profiles/safe.json and config/profiles/normal.json)"
            )

        self.image = self._load_image(runtime_json.get("image", {}))

    @staticmethod
    def _load_image(data: dict) -> ImageLimits:
        try:
            return ImageLimits(**{k: v for k, v in data.items() if not k.startswith("$")})
        except Exception as exc:
            raise ConfigError(f"runtime.json 'image' section invalid: {exc}") from exc

    def _load_profiles(self, hardware_json: dict) -> dict[str, HardwareProfile]:
        """Load profiles from config/profiles/*.json (file stem = profile id).

        Falls back to inline hardware.json["profiles"] for the pre-Phase-9
        layout. '$'-prefixed comment keys are ignored.
        """
        profiles: dict[str, HardwareProfile] = {}
        rel_dir = hardware_json.get("profiles_dir", "config/profiles")
        profiles_dir = self.root / rel_dir
        if profiles_dir.is_dir():
            for path in sorted(profiles_dir.glob("*.json")):
                try:
                    data = _read_json(path)
                    values = {k: v for k, v in data.items() if not k.startswith("$")}
                    profiles[path.stem] = HardwareProfile(**values)
                except Exception as exc:
                    raise ConfigError(f"profile {rel_dir}/{path.name} invalid: {exc}") from exc
        if not profiles:
            for name, values in hardware_json.get("profiles", {}).items():
                clean = {k: v for k, v in values.items() if not k.startswith("$")}
                profiles[name] = HardwareProfile(**clean)
        return profiles

    # --- resolved absolute paths (root + relative config value) ---

    @property
    def skills_dir(self) -> Path:
        return self.root / self.paths.skills_dir

    @property
    def storage_dir(self) -> Path:
        return self.root / self.paths.storage_dir

    @property
    def database_path(self) -> Path:
        return self.root / self.paths.database_file

    @property
    def logs_dir(self) -> Path:
        return self.root / self.paths.logs_dir

    @property
    def rag_uploads_dir(self) -> Path:
        return self.root / self.paths.rag_uploads_dir

    @property
    def rag_db_path(self) -> Path:
        return self.root / self.paths.rag_database_file

    @property
    def image_temp_dir(self) -> Path:
        return self.root / self.image.temp_dir_relative

    @property
    def tesseract_path(self) -> Path:
        return self.root / self.image.tesseract_relative

    @property
    def tessdata_path(self) -> Path:
        return self.root / self.image.tessdata_relative


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def load_config(root: Path) -> PocketAIConfig:
    root = Path(root)
    config_dir = root / "config"
    return PocketAIConfig(
        root=root,
        model_json=_read_json(config_dir / "model.json"),
        hardware_json=_read_json(config_dir / "hardware.json"),
        runtime_json=_read_json(config_dir / "runtime.json"),
    )
