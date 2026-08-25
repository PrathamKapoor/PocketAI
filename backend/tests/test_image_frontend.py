"""Runs the jsdom frontend DOM test for the image-input UI.

The frontend is vanilla JS; this shells out to Node + jsdom to exercise the
real DOM (clipboard paste -> preview, remove, upload, send payload). It is a
dev-only test: if Node or jsdom is unavailable it is skipped so the portable
runtime never depends on them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEST_JS = ROOT / "frontend" / "tests" / "image_input.test.mjs"


def _node() -> str | None:
    return shutil.which("node") or shutil.which("node.exe")


def test_image_frontend_dom():
    node = _node()
    if node is None:
        raise pytest.skip("node not on PATH")
    if not TEST_JS.is_file():
        raise pytest.skip("frontend test harness missing")
    env = dict(os.environ)
    # Let the harness find jsdom if it lives in a non-default location.
    proc = subprocess.run(
        [node, str(TEST_JS)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    print(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr, file=sys.stderr)
    if proc.returncode == 77:
        raise pytest.skip("jsdom not installed")
    assert proc.returncode == 0, "frontend image-input DOM test failed"
