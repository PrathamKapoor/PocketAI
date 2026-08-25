"""RAG performance harness + synthetic document generator.

Usage (from the PocketAI root):

    python -m rag.benchmark            # default corpus
    python -m rag.benchmark --docs 40  # bigger corpus

Measures the three numbers Phase 7 cares about: indexing time, memory
usage of the index, and retrieval latency. Everything runs against
rag/work/bench, which is deleted first and git-ignored.

`make_sample_pdf` is also imported by the test suite and by Phase 10's
large-PDF scenario, so real PDF parsing is covered without bundling
binary assets in the repo.
"""

from __future__ import annotations

import argparse
import ctypes
import shutil
import statistics
import time
from pathlib import Path

from rag.pipeline import RagPipeline

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "rag" / "work" / "bench"

_TOPICS = [
    ("database", "index query table schema transaction lock vacuum btree"),
    ("network", "socket packet latency routing protocol handshake timeout dns"),
    ("security", "authentication token encryption firewall audit session nonce"),
    ("compiler", "parser lexer syntax grammar optimization register bytecode"),
    ("memory", "allocation heap stack garbage collector fragmentation cache"),
]


def make_sample_pdf(page_texts: list[str]) -> bytes:
    """Build a minimal valid multi-page PDF (Helvetica text pages)."""
    font_id = 3
    objs: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    kids: list[str] = []
    next_id = 4
    for text in page_texts:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        kids.append(f"{page_id} 0 R")
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines = escaped.split("\n")
        stream = (
            "BT /F1 11 Tf 50 780 Td 14 TL\n"
            + "\nT*\n".join(f"({line}) Tj" for line in lines)
            + "\nET"
        ).encode("latin-1", "replace")
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            f" /Resources << /Font << /F1 {font_id} 0 R >> >>"
            f" /Contents {content_id} 0 R >>"
        ).encode()
        objs[content_id] = (
            b"<< /Length " + str(len(stream)).encode()
            + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objs[2] = (
        "<< /Type /Pages /Kids [" + " ".join(kids)
        + f"] /Count {len(kids)} >>"
    ).encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for obj_id in sorted(objs):
        offsets[obj_id] = len(out)
        out += f"{obj_id} 0 obj\n".encode() + objs[obj_id] + b"\nendobj\n"
    xref_pos = len(out)
    max_id = max(objs)
    out += f"xref\n0 {max_id + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for i in range(1, max_id + 1):
        out += f"{offsets[i]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode()
    return bytes(out)


def make_corpus_text(doc_index: int, paragraphs: int = 60) -> str:
    """Deterministic pseudo-technical text with a distinct topic per doc."""
    topic, keywords = _TOPICS[doc_index % len(_TOPICS)]
    words = keywords.split()
    lines = []
    for p in range(paragraphs):
        sentence_bits = []
        for s in range(4):
            pick = " ".join(words[(p + s + i) % len(words)] for i in range(6))
            sentence_bits.append(f"Doc{doc_index} paragraph{p} covers {pick} on {topic}.")
        lines.append(" ".join(sentence_bits))
    return f"Chapter {topic} (document {doc_index})\n\n" + "\n\n".join(lines)


def _free_ram_mb() -> int:
    """Free physical RAM via stdlib only (mirrors backend.tools.sysinfo)."""
    try:
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
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullAvailPhys // (1024 * 1024))
    except Exception:
        return -1


def run(docs: int, pdf_pages: int) -> None:
    shutil.rmtree(BENCH_DIR, ignore_errors=True)
    pipeline = RagPipeline(
        uploads_dir=BENCH_DIR / "uploads",
        db_path=BENCH_DIR / "documents.db",
    )

    print(f"Corpus: {docs} TXT docs (~60 paragraphs each) + 1 PDF ({pdf_pages} pages)")
    ram_before = _free_ram_mb()

    started = time.perf_counter()
    for i in range(docs):
        pipeline.index_upload(f"notes_{i}.txt", make_corpus_text(i).encode())
    pdf_bytes = make_sample_pdf(
        [f"Page {i} about database indexing and query planning." for i in range(pdf_pages)]
    )
    pipeline.index_upload("manual.pdf", pdf_bytes)
    index_secs = time.perf_counter() - started

    ram_after = _free_ram_mb()
    total_chunks = pipeline.retriever.chunk_count()
    print(f"Indexing: {index_secs:.2f}s total"
          f" ({index_secs / (docs + 1) * 1000:.0f} ms/doc), {total_chunks} chunks")
    if ram_before > 0 and ram_after > 0:
        print(f"Free RAM delta during indexing: {ram_before - ram_after} MB"
              " (includes OS noise)")

    queries = [
        "database index query planning",
        "socket timeout handshake",
        "authentication token audit",
        "parser grammar optimization",
        "garbage collector fragmentation",
    ] * 10
    latencies = []
    for i, q in enumerate(queries):
        t0 = time.perf_counter()
        hits = pipeline.search(q, top_k=6)
        latencies.append((time.perf_counter() - t0) * 1000)
        if i == 0:
            top = hits[0] if hits else None
            print(f"Sample query {q!r} -> {len(hits)} hits"
                  + (f", top: {top['filename']}#{top['chunk_index']}" if top else ""))
    latencies.sort()
    print(f"Retrieval latency over {len(queries)} queries:"
          f" mean {statistics.mean(latencies):.2f} ms,"
          f" p95 {latencies[int(len(latencies) * 0.95)]:.2f} ms,"
          f" max {max(latencies):.2f} ms")

    t0 = time.perf_counter()
    ctx = pipeline.context_for("database index query planning")
    print(f"context_for(): {(time.perf_counter() - t0) * 1000:.2f} ms,"
          f" {len(ctx or '')} chars of prompt context")

    pipeline.close()
    shutil.rmtree(BENCH_DIR, ignore_errors=True)
    print("Benchmark workspace cleaned up.")


def main() -> None:
    parser = argparse.ArgumentParser(description="PocketAI RAG benchmark")
    parser.add_argument("--docs", type=int, default=20)
    parser.add_argument("--pdf-pages", type=int, default=100)
    args = parser.parse_args()
    run(args.docs, args.pdf_pages)


if __name__ == "__main__":
    main()
