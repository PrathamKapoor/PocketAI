"""Tests for CPU core detection (replaces PowerShell dependency)."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from runtime.detect_cores import detect_cores, main


def test_detect_cores_returns_valid_values():
    """detect_cores should return (physical, logical) with valid positive values."""
    physical, logical = detect_cores()
    assert physical >= 1
    assert logical >= physical


def test_detect_cores_logical_not_less_than_physical():
    """Logical cores should never be less than physical cores."""
    physical, logical = detect_cores()
    assert logical >= physical


def test_detect_cores_fallback_on_windows():
    """On Windows, should use ctypes detection or fall back to defaults."""
    if sys.platform == "win32":
        physical, logical = detect_cores()
        # Should get real values or sensible defaults
        assert physical >= 1
        assert logical >= physical
    else:
        pytest.skip("Not on Windows")


def test_detect_cores_fallback_when_cpu_count_returns_none():
    """Should handle os.cpu_count() returning None."""
    with patch("os.cpu_count", return_value=None):
        physical, logical = detect_cores()
        assert physical >= 1
        assert logical >= physical


def test_detect_cores_fallback_when_ctypes_fails():
    """Should handle ctypes detection failure gracefully."""
    if sys.platform != "win32":
        pytest.skip("Not on Windows")

    with patch("runtime.detect_cores._windows_physical_cores", return_value=None):
        physical, logical = detect_cores()
        # Should fall back to min(4, logical) for physical
        assert physical >= 1
        assert logical >= physical


def test_main_prints_output(capsys):
    """main() should print THREADS=<n> and THREADS_BATCH=<n>."""
    main()
    captured = capsys.readouterr()
    lines = captured.out.strip().split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("THREADS=")
    assert lines[1].startswith("THREADS_BATCH=")
    threads = int(lines[0].split("=")[1])
    threads_batch = int(lines[1].split("=")[1])
    assert threads >= 1
    assert threads_batch >= threads
