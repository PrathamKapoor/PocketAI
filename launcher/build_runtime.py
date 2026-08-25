"""Build the portable Python runtime (runtime\\python) for USB deployment.

Requires internet ONCE, at build time. Downloads the official Python
embeddable package, enables site-packages, bootstraps pip, and installs
backend\\requirements.txt into it. The result is fully self-contained:
the target machine needs neither Python nor internet.

Usage (from the PocketAI root, with any Python 3.10+):

    python launcher\\build_runtime.py            # build
    python launcher\\build_runtime.py --force    # rebuild over an existing runtime\\python

The embeddable package is the official CPython Windows distribution for
this exact purpose (docs.python.org/3/using/windows.html#the-embeddable-package).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PY = ROOT / "runtime" / "python"

PY_VERSION = "3.13.14"  # keep in sync with the version the backend is tested on
ZIP_URL = (
    f"https://www.python.org/ftp/python/{PY_VERSION}/"
    f"python-{PY_VERSION}-embed-amd64.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
PTH_NAME = "python313._pth"  # major+minor, no dot


def run(cmd: list[str]) -> None:
    print(f"[build] {' '.join(cmd)}")
    # PYTHONNOUSERSITE=1: never let packages installed for some other
    # Python on this machine satisfy the bundle's requirements (pip would
    # report 'already satisfied' and skip installing into runtime\python).
    env = {**os.environ, "PYTHONNOUSERSITE": "1"}
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    if result.returncode != 0:
        sys.exit(f"[build] command failed with exit code {result.returncode}")


def download(url: str, dest: Path) -> None:
    print(f"[build] downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)


def main() -> None:
    force = "--force" in sys.argv
    if RUNTIME_PY.exists():
        if not force:
            sys.exit(
                f"[build] {RUNTIME_PY} already exists. "
                "Re-run with --force to delete and rebuild it."
            )
        print(f"[build] removing existing {RUNTIME_PY}")
        shutil.rmtree(RUNTIME_PY)
    RUNTIME_PY.mkdir(parents=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "python-embed.zip"
        download(ZIP_URL, zip_path)
        print(f"[build] extracting to {RUNTIME_PY}")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(RUNTIME_PY)

        # Enable site-packages: the embeddable dist ships with 'import site'
        # commented out, which would block pip-installed packages.
        pth = RUNTIME_PY / PTH_NAME
        text = pth.read_text(encoding="utf-8")
        pth.write_text(text.replace("#import site", "import site"), encoding="utf-8")
        print(f"[build] enabled site-packages in {PTH_NAME}")

        get_pip = tmp_path / "get-pip.py"
        download(GET_PIP_URL, get_pip)

        python_exe = RUNTIME_PY / "python.exe"
        run([str(python_exe), str(get_pip), "--no-warn-script-location"])

    run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "-r",
            str(ROOT / "backend" / "requirements.txt"),
        ]
    )
    run(
        [
            str(python_exe),
            "-c",
            "import sys, os, fastapi, uvicorn, httpx, pypdf, pydantic, multipart; "
            "base = os.path.dirname(sys.executable); "
            "mods = [fastapi, uvicorn, httpx, pypdf, pydantic]; "
            "bad = [m.__name__ for m in mods "
            "if not (m.__file__ or '').lower().startswith(base.lower())]; "
            "assert not bad, f'deps not bundled from runtime (loaded from elsewhere): {bad}'; "
            "print('[build] runtime OK:', sys.version.split()[0], "
            "'- all deps load from', base)",
        ]
    )
    print(f"[build] done. Portable runtime ready at {RUNTIME_PY}")


if __name__ == "__main__":
    main()
