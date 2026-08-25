"""PocketAI Phase-4 benchmark harness (dev-time tool).

Launches llama-cli against the Qwen3.5-4B model and measures:
  - model load time        (parsed from llama-cli output)
  - prompt / eval tok/s    (parsed from llama-cli output)
  - peak RAM working set   (sampled via Windows GetProcessMemoryInfo)
  - CPU utilization        (sampled via GetProcessTimes)

Runs on the DEV machine using system Python. On-target benchmarking is done
later with the bundled runtime. All paths are relative to the PocketAI root.
"""
import ctypes
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # pocket_ai
LLAMA_DIR = os.path.join(ROOT, "runtime", "llama.cpp")
LLAMA_CLI = os.path.join(LLAMA_DIR, "llama-completion.exe")  # single-shot, exits cleanly
MODEL = os.path.join(ROOT, "models", "Qwen3.5-4B-Q4_K_M.gguf")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _GetProcessMemoryInfo = psapi.GetProcessMemoryInfo
except OSError:
    _GetProcessMemoryInfo = kernel32.K32GetProcessMemoryInfo


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def physical_cores():
    """Count physical cores via GetLogicalProcessorInformation."""
    length = wintypes.DWORD(0)
    kernel32.GetLogicalProcessorInformation(None, ctypes.byref(length))
    if length.value == 0:
        return os.cpu_count() or 4
    buf = (ctypes.c_ubyte * length.value)()
    if not kernel32.GetLogicalProcessorInformation(buf, ctypes.byref(length)):
        return os.cpu_count() or 4
    struct_size = 24  # sizeof(SYSTEM_LOGICAL_PROCESSOR_INFORMATION) on x64
    cores = 0
    for i in range(length.value // struct_size):
        off = i * struct_size
        rel = int.from_bytes(bytes(buf[off + 8:off + 12]), "little")
        if rel == 0:  # RelationProcessorCore
            cores += 1
    return cores or (os.cpu_count() or 4)


def get_ws(pid):
    """Return current working set (bytes) for a PID, or None."""
    h = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)  # QUERY_INFORMATION | VM_READ
    if not h:
        return None
    try:
        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(pmc)
        if _GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize
        return None
    finally:
        kernel32.CloseHandle(h)


def get_cpu_time(handle):
    """Return total CPU seconds (kernel+user) for a process handle."""
    creation = wintypes.FILETIME()
    exitft = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    if not kernel32.GetProcessTimes(handle,
                                    ctypes.byref(creation), ctypes.byref(exitft),
                                    ctypes.byref(kernel), ctypes.byref(user)):
        return None
    def to_sec(f):
        return (f.dwHighDateTime << 32 | f.dwLowDateTime) / 1e7
    return to_sec(kernel) + to_sec(user)


def sample_loop(proc, stop, peak):
    logical = os.cpu_count() or 1
    h = kernel32.OpenProcess(0x0400 | 0x0010, False, proc.pid)
    last_wall = time.time()
    last_cpu = get_cpu_time(h) if h else None
    while not stop.is_set():
        ws = get_ws(proc.pid)
        if ws and ws > peak["ws"]:
            peak["ws"] = ws
        if h and last_cpu is not None:
            now = time.time()
            cpu = get_cpu_time(h)
            if cpu is not None and now > last_wall:
                dcpu = cpu - last_cpu
                dwall = now - last_wall
                util = dcpu / (dwall * logical) * 100
                peak["cpu_samples"].append(util)
                last_cpu, last_wall = cpu, now
        time.sleep(0.25)
    if h:
        kernel32.CloseHandle(h)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ctx = 4096
    threads = physical_cores()
    batch = 512
    n_gen = 200
    prompt = ("You are PocketAI, a helpful offline assistant. "
              "In one short sentence, explain what a USB drive is.")

    cmd = [
        LLAMA_CLI,
        "-m", MODEL,
        "-c", str(ctx),
        "-t", str(threads),
        "-b", str(batch),
        "-ub", str(batch),
        "-n", str(n_gen),
        "-p", prompt,
        "--no-display-prompt",
        "-no-cnv",               # disable auto conversation mode (pure single-shot)
        "--simple-io",           # clean output for subprocess capture
        "-ngl", "0",             # force CPU-only for a representative target number
        "--no-warmup",
    ]
    print("=" * 66)
    print("PocketAI Phase-4 benchmark")
    print("=" * 66)
    print(f"model      : {os.path.basename(MODEL)} ({os.path.getsize(MODEL)/1e9:.2f} GB)")
    print(f"context    : {ctx}")
    print(f"threads    : {threads} (physical cores)")
    print(f"batch      : {batch}")
    print(f"gen tokens : {n_gen}")
    print(f"cmd        : {' '.join(os.path.basename(c) if i==0 else c for i,c in enumerate(cmd))}")
    print("-" * 66)

    peak = {"ws": 0, "cpu_samples": []}
    stop = threading.Event()
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace",
                            cwd=LLAMA_DIR,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    sampler = threading.Thread(target=sample_loop, args=(proc, stop, peak), daemon=True)
    sampler.start()

    out = []
    for line in proc.stdout:
        out.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    proc.wait()
    wall = time.time() - t0
    stop.set()
    sampler.join(timeout=2)
    text = "".join(out)

    print("-" * 66)
    print("PARSED METRICS")
    print("-" * 66)
    m = re.search(r"load time\s*=\s*([\d.]+)\s*ms", text)
    load_ms = float(m.group(1)) if m else None
    m = re.search(r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)", text)
    prompt_ms, prompt_tok = (float(m.group(1)), int(m.group(2))) if m else (None, None)
    m = re.search(r"eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)", text)
    eval_ms, eval_tok = (float(m.group(1)), int(m.group(2))) if m else (None, None)
    m = re.search(r"eval time\s*=.*?([\d.]+)\s*tokens?/s", text) or \
        re.search(r"=\s*([\d.]+)\s*tokens?/s", text)

    def fmt(x, unit=""):
        return f"{x:.2f}{unit}" if x is not None else "n/a"

    print(f"exit code          : {proc.returncode}")
    print(f"total wall time    : {fmt(wall,' s')}")
    print(f"model load time    : {fmt(load_ms/1000 if load_ms else None,' s')} ({fmt(load_ms,' ms')})")
    if prompt_ms:
        print(f"prompt eval        : {prompt_tok} tok in {fmt(prompt_ms,' ms')} -> {fmt(prompt_tok/(prompt_ms/1000),' tok/s')}")
    if eval_ms and eval_tok:
        print(f"generation eval    : {eval_tok} tok in {fmt(eval_ms,' ms')} -> {fmt(eval_tok/(eval_ms/1000),' tok/s')}")
    print(f"peak RAM (ws)      : {fmt(peak['ws']/1e9,' GB')} ({peak['ws']//1024//1024} MB)")
    if peak["cpu_samples"]:
        avg_cpu = sum(peak["cpu_samples"]) / len(peak["cpu_samples"])
        print(f"avg CPU utilization: {fmt(avg_cpu,' %')} (of {os.cpu_count()} logical CPUs)")
    print("=" * 66)
    if proc.returncode != 0:
        print("!! llama-cli exited non-zero — inspect output above.")
        sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
