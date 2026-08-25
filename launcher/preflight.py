"""PocketAI pre-flight checks (Phase 9 launcher).

Run by launcher\\START_AI.bat BEFORE anything is started. Validates that
PocketAI can run on THIS machine and decides the hardware profile:

1. Python version + backend dependencies importable
2. Config files load (config/model.json, hardware.json, runtime.json, profiles)
3. Required files exist (llama-server.exe, model .gguf, backend, frontend)
4. RAM measured -> hardware profile selected (safe / normal / performance)
5. Ports 8090 (backend) and 8091 (model server) free, or already running US

Human-readable diagnostics go to stdout. Machine-readable results are
written to logs/preflight.env as KEY=value lines for the .bat launcher:

    OK=1  PROFILE=normal  CTX=8192  TOTAL_MB=...  FREE_MB=...
    BACKEND_PORT=8090  MODEL_PORT=8091  MODEL_READY=0  BACKEND_READY=0

Exit code 0 = safe to launch, 1 = aborted with a printed reason.
This script is stdlib-only: it must produce a useful error even when the
backend dependencies are missing.
"""

from __future__ import annotations

import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_MODULES = ("pydantic", "fastapi", "uvicorn", "httpx", "pypdf")
MIN_PYTHON = (3, 10)


def say(msg: str) -> None:
    print(f"[PocketAI] {msg}")


def fail(reason: str, hint: str = "") -> int:
    say(f"PREFLIGHT FAILED: {reason}")
    if hint:
        say(f"  -> {hint}")
    write_env({"OK": "0", "REASON": reason.replace("=", " ")})
    return 1


def write_env(values: dict[str, str]) -> None:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    lines = [f"{k}={v}" for k, v in values.items()]
    (logs / "preflight.env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_python() -> None | str:
    if sys.version_info < MIN_PYTHON:
        return (
            f"Python {sys.version.split()[0]} is too old"
            f" (need >= {'.'.join(map(str, MIN_PYTHON))})"
        )
    return None


def check_dependencies() -> list[str]:
    missing = []
    for name in REQUIRED_MODULES:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    return missing


def http_healthy(url: str, api_key: str = "", timeout: float = 2.0) -> bool:
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def port_state(host: str, port: int, probe_url: str, api_key: str = "") -> str:
    """Return 'ours' (healthy PocketAI service), 'free', or 'busy'."""
    if http_healthy(probe_url, api_key):
        return "ours"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return "free"
        except OSError:
            return "busy"


def _kill_our_llama_server() -> bool:
    """Kill a llama-server.exe whose image path is under THIS PocketAI root.

    Used to recover from a stale/crashed previous session that still holds the
    model port. Returns True if we believe the port is now free. Fail-safe: any
    error (wmic missing, parse failure, permission) leaves the port alone so we
    never touch a process we cannot positively identify as ours.
    """
    import subprocess

    try:
        out = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='llama-server.exe'",
                "get",
                "CommandLine,ProcessId",
                "/format:csv",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    marker = str(ROOT / "runtime" / "llama.cpp").replace("\\", "/").lower()
    pids = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        # CSV columns: Node, CommandLine, ProcessId (order not guaranteed by name).
        if any(marker in p.lower() for p in parts) and any(p.isdigit() for p in parts):
            for p in parts:
                if p.isdigit():
                    pids.append(p)
                    break
    if not pids:
        return False
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", pids[0]],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def recover_busy_model_port(model_port: int, api_key: str, host: str) -> str:
    """If the model port is bound by a stale process, try to free it.

    Returns the new port_state: 'free' if we killed and the port is now open,
    or 'busy' if recovery was not possible/safe.
    """
    if not _kill_our_llama_server():
        return "busy"
    # Give the OS a moment to release the socket, then re-check.
    import time

    for _ in range(10):
        time.sleep(0.5)
        if port_state(host, model_port, f"http://{host}:{model_port}/health", api_key) in (
            "free",
            "ours",
        ):
            return "free"
    return "busy"


def main() -> int:
    say("Pre-flight checks")
    say(f"  Root: {ROOT}")

    # --- 1. Python itself ---
    problem = check_python()
    if problem:
        return fail(problem, "Install Python 3.10+ or restore runtime\\python.")

    # --- 2. Backend dependencies ---
    missing = check_dependencies()
    if missing:
        return fail(
            f"missing Python packages: {', '.join(missing)}",
            "Run launcher\\install_deps.bat (needs internet once), or restore "
            "the bundled runtime\\python folder.",
        )

    # --- 3. Config ---
    try:
        from backend.config.loader import load_config
    except Exception as exc:
        return fail(f"cannot import backend: {exc}")
    try:
        cfg = load_config(ROOT)
    except Exception as exc:
        return fail(str(exc), "Check the config\\ folder is complete.")

    # --- 4. Required files ---
    model_rel = cfg.model_info.get("file", "models/Qwen3.5-4B-Q4_K_M.gguf")
    required = {
        "model server": ROOT / "runtime" / "llama.cpp" / "llama-server.exe",
        "model file": ROOT / model_rel,
        "backend": ROOT / "backend" / "main.py",
        "frontend": ROOT / "frontend" / "index.html",
    }
    for label, path in required.items():
        if not path.is_file():
            return fail(
                f"missing {label}: {path.relative_to(ROOT)}",
                "Re-copy the full PocketAI folder to this drive.",
            )

    # --- 4b. OCR engine (bundled Tesseract) — OPTIONAL but warned. ---
    # Image input degrades to "OCR unavailable" if this is missing, so a copy
    # that dropped runtime/ocr/ must be caught here, not at first image use.
    ocr_bin = ROOT / cfg.image.tesseract_relative
    ocr_data = ROOT / cfg.image.tessdata_relative / "eng.traineddata"
    ocr_ok = ocr_bin.is_file() and ocr_data.is_file()
    if not ocr_ok:
        say(
            "  WARNING: bundled OCR engine (runtime/ocr/) not found. "
            "Image paste/upload will report 'OCR unavailable'."
        )
        say("    -> Re-copy the full PocketAI folder to restore OCR.")

    # --- 5. RAM + profile ---
    try:
        from backend.tools.hardware import select_profile
        from backend.tools.sysinfo import get_memory
    except Exception as exc:
        return fail(f"cannot load hardware tools: {exc}")
    mem = get_memory()
    profile_name, profile = select_profile(cfg, mem)
    say(
        f"  RAM: {mem.total_mb} MB total, {mem.available_mb} MB available"
        f"  ->  profile: {profile_name.upper()}"
        f" (ctx={profile.recommended_server_context},"
        f" max_gen={profile.max_generation_tokens})"
    )

    # --- 6. Ports ---
    host = cfg.backend.host
    backend_port = cfg.backend.port
    model_port = cfg.model_server.port
    model_state = port_state(
        host, model_port, f"http://{host}:{model_port}/health",
        api_key=cfg.model_server.api_key,
    )
    backend_state = port_state(
        host, backend_port, f"http://{host}:{backend_port}/health"
    )
    for label, state, port in (
        ("model server", model_state, model_port),
        ("backend", backend_state, backend_port),
    ):
        if state == "busy":
            if label == "model server":
                # A stale/crashed previous session may still hold the port.
                # Recover by killing our own llama-server, then re-check.
                say(f"  Port {port} bound by a stale process; attempting recovery...")
                model_state = recover_busy_model_port(model_port, cfg.model_server.api_key, host)
                if model_state == "free":
                    say(f"  Port {port} (model server): freed")
                    continue
            elif label == "backend":
                # Check if it's a stale PocketAI backend
                if http_healthy(f"http://{host}:{port}/health"):
                    say(f"  Port {port} (backend): already running PocketAI backend")
                    backend_state = "ours"
                    continue
            return fail(
                f"port {port} ({label}) is already used by another program",
                f"Close the other program, run launcher\\STOP_AI.bat, or "
                f"change the port in config\\ (then update the mirror in "
                f"runtime\\start_model.bat).",
            )
        status = "already running" if state == "ours" else "free"
        say(f"  Port {port} ({label}): {status}")

    write_env(
        {
            "OK": "1",
            "PROFILE": profile_name,
            "CTX": str(profile.recommended_server_context),
            "TOTAL_MB": str(mem.total_mb),
            "FREE_MB": str(mem.available_mb),
            "BACKEND_HOST": host,
            "BACKEND_PORT": str(backend_port),
            "MODEL_PORT": str(model_port),
            "MODEL_READY": "1" if model_state == "ours" else "0",
            "BACKEND_READY": "1" if backend_state == "ours" else "0",
            "OCR_OK": "1" if ocr_ok else "0",
        }
    )
    say("  Pre-flight OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
