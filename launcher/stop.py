"""PocketAI - stop.py (Phase 9 launcher, stdlib-only, no PowerShell).

Stops the PocketAI processes that were started from THIS PocketAI root.
Processes are matched by path/command line so other copies or unrelated
apps are left alone. Uses only wmic.exe + taskkill.exe (always present on
Windows), never PowerShell, so teardown works even when PowerShell is
restricted on locked-down school PCs.

Usage:
    python stop.py            # stop both backend and model server
    python stop.py --backend  # stop only the backend
    python stop.py --model    # stop only the model server
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ROOT_MARKER = str(ROOT).replace("\\", "/").lower()


def _wmic(query: str, columns: str) -> list[dict[str, str]]:
    """Run a wmic query and return rows of {column: value}."""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", query, "get", columns, "/format:csv"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, str]] = []
    cols = [c.strip() for c in columns.split(",")]
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != len(cols):
            continue
        if not any(parts):
            continue
        rows.append(dict(zip(cols, parts)))
    return rows


def _kill(pid: str, label: str) -> None:
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", pid],
            capture_output=True,
            text=True,
            timeout=15,
        )
        print(f"[PocketAI] stopped {label} (PID {pid})")
    except (OSError, subprocess.SubprocessError):
        pass


def stop_backend() -> None:
    found = False
    for row in _wmic("name='python.exe' OR name='pythonw.exe'", "CommandLine,ProcessId"):
        cmd = (row.get("CommandLine") or "").replace("\\", "/").lower()
        pid = row.get("ProcessId")
        if not pid:
            continue
        # Only our backend: references backend/main.py AND this root.
        if "backend/main.py" in cmd and ROOT_MARKER in cmd:
            _kill(pid, "backend")
            found = True
    if not found:
        print("[PocketAI] backend is not running")


def stop_model() -> None:
    llama_dir = (ROOT / "runtime" / "llama.cpp").resolve()
    marker = str(llama_dir).replace("\\", "/").lower()
    found = False
    for row in _wmic("name='llama-server.exe'", "ExecutablePath,ProcessId"):
        path = (row.get("ExecutablePath") or "").replace("\\", "/").lower()
        pid = row.get("ProcessId")
        if not pid:
            continue
        if path.startswith(marker):
            _kill(pid, "model server")
            found = True
    if not found:
        print("[PocketAI] no PocketAI model server is running")


def main() -> int:
    args = sys.argv[1:]
    want_backend = "--backend" in args
    want_model = "--model" in args
    if not want_backend and not want_model:
        want_backend = want_model = True

    if want_model:
        stop_model()
    if want_backend:
        stop_backend()
    return 0


if __name__ == "__main__":
    sys.exit(main())
