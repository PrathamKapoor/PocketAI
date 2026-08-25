"""Hardware detection using only the standard library.

Windows-first (the PocketAI target) via ctypes — no psutil dependency, so the
embeddable Python build stays small. Linux (/proc) is supported for dev
convenience; unknown platforms report zeros, which the profile selector treats
as "unknown machine -> SAFE mode".
"""

from __future__ import annotations

import ctypes
import os
import platform
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryInfo:
    total_mb: int
    available_mb: int


@dataclass(frozen=True)
class CpuInfo:
    logical_cores: int
    physical_cores: int
    arch: str


def _windows_memory() -> MemoryInfo:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        raise OSError("GlobalMemoryStatusEx failed")
    mb = 1024 * 1024
    return MemoryInfo(stat.ullTotalPhys // mb, stat.ullAvailPhys // mb)


def _linux_memory() -> MemoryInfo:
    info: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[-1] == "kB":
                info[key.strip()] = int(parts[0]) // 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    return MemoryInfo(total, available)


def get_memory() -> MemoryInfo:
    try:
        if sys.platform == "win32":
            return _windows_memory()
        if sys.platform.startswith("linux"):
            return _linux_memory()
    except OSError:
        pass
    return MemoryInfo(0, 0)


class _SLPI(ctypes.Structure):
    """SYSTEM_LOGICAL_PROCESSOR_INFORMATION (mask + relationship + 16B union)."""

    _fields_ = [
        ("ProcessorMask", ctypes.c_size_t),
        ("Relationship", ctypes.c_int),
        ("Data", ctypes.c_uint32 * 4),
    ]


_RELATION_PROCESSOR_CORE = 0


def _windows_physical_cores() -> int | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    return cores or None


def get_cpu() -> CpuInfo:
    logical = os.cpu_count() or 1
    physical: int | None = None
    if sys.platform == "win32":
        try:
            physical = _windows_physical_cores()
        except OSError:
            physical = None
    return CpuInfo(
        logical_cores=logical,
        physical_cores=physical or logical,
        arch=platform.machine() or "unknown",
    )
