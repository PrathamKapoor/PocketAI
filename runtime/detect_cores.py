"""Detect CPU cores for llama.cpp thread configuration.

Prints THREADS=<physical cores> and THREADS_BATCH=<logical processors>,
one KEY=value per line, for consumption by the .bat launchers via for /f.
Falls back to conservative defaults if detection fails.

This script is stdlib-only: it must work without external dependencies.
"""

from __future__ import annotations

import ctypes
import os
import sys


def _windows_physical_cores() -> int | None:
    """Detect physical CPU cores via Windows API."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _SLPI(ctypes.Structure):
            _fields_ = [
                ("ProcessorMask", ctypes.c_size_t),
                ("Relationship", ctypes.c_int),
                ("Data", ctypes.c_uint32 * 4),
            ]

        _RELATION_PROCESSOR_CORE = 0

        size = ctypes.c_ulong(0)
        kernel32.GetLogicalProcessorInformation(None, ctypes.byref(size))
        if size.value < ctypes.sizeof(_SLPI):
            return None

        count = size.value // ctypes.sizeof(_SLPI)
        buf = (_SLPI * count)()
        length = ctypes.c_ulong(ctypes.sizeof(buf))
        if not kernel32.GetLogicalProcessorInformation(buf, ctypes.byref(length)):
            return None

        cores = sum(1 for item in buf if item.Relationship == _RELATION_PROCESSOR_CORE)
        return cores if cores > 0 else None
    except (OSError, AttributeError):
        return None


def detect_cores() -> tuple[int, int]:
    """Return (physical_cores, logical_cores).

    Falls back to sensible defaults (4/8) if detection fails.
    """
    logical = os.cpu_count() or 8
    physical = None

    if sys.platform == "win32":
        physical = _windows_physical_cores()

    if physical is None or physical < 1:
        physical = min(4, logical)

    if logical < physical:
        logical = physical

    return physical, logical


def main() -> None:
    physical, logical = detect_cores()
    print(f"THREADS={physical}")
    print(f"THREADS_BATCH={logical}")


if __name__ == "__main__":
    main()
