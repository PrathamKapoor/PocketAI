"""Offline OCR via the bundled portable Tesseract binary.

This is the vision strategy for PocketAI: the local Qwen model is text-only, so
an image becomes *text* that the existing supervisor/skills pipeline can reason
about. Tesseract runs as a subprocess; no network, no cloud, no extra RAM-heavy
framework. Several page-segmentation modes (PSM) are tried and the most useful
result is kept, which makes prose, code and sparse-diagram screenshots all read
reasonably well.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from backend.image.errors import OcrError

# PSM candidates tried for every image. 3 = fully automatic, 6 = uniform block
# of text (good for code/lists), 11 = sparse text (good for diagrams/slides).
_PSM_CANDIDATES = (3, 6, 11)
_OCR_TIMEOUT_SECONDS = 60
# Tesseract language/script codes are short alphanumeric tokens (eng, osd, ...).
_OCR_LANGUAGE_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass
class OcrResult:
    text: str
    confidence: float  # mean word confidence 0-100, or 0.0 when none


def _parse_tsv(tsv: str) -> tuple[str, float]:
    """Turn Tesseract TSV into (reconstructed text, mean word confidence).

    Line breaks and blank lines between blocks are preserved so paragraphs,
    code and tables stay legible for the model.
    """
    lines_out: list[str] = []
    cur_line: list[str] = []
    cur_key = (-1, -1, -1, -1)
    conf_sum = 0.0
    conf_n = 0

    for raw in tsv.splitlines()[1:]:  # skip header
        cols = raw.split("\t")
        if len(cols) < 12:
            continue
        try:
            level = int(cols[0])
            block = int(cols[2])
            par = int(cols[3])
            line = int(cols[4])
            conf = float(cols[10])
            word = cols[11]
        except (ValueError, IndexError):
            continue
        if level != 5 or not word:  # only real words contribute text
            continue
        conf_sum += conf
        conf_n += 1
        key = (1, block, par, line)  # page fixed at 1
        if key != cur_key and cur_line:
            lines_out.append(" ".join(cur_line))
            # Blank line between blocks for spacing.
            if key[2] != cur_key[2] and cur_key[2] != -1:
                lines_out.append("")
            cur_line = []
        cur_key = key
        cur_line.append(word)
    if cur_line:
        lines_out.append(" ".join(cur_line))

    text = "\n".join(lines_out).strip()
    confidence = (conf_sum / conf_n) if conf_n else 0.0
    return text, confidence


def run_ocr(
    image_path: str | Path,
    *,
    tesseract_path: str | Path,
    tessdata_path: str | Path,
    language: str = "eng",
) -> OcrResult:
    """Run Tesseract over ``image_path`` and return OCR text + confidence.

    Raises OcrError if the engine is missing, times out, or exits non-zero.
    The temporary TSV output is always removed, including on timeout.
    """
    tesseract = Path(tesseract_path)
    if not tesseract.is_file():
        raise OcrError(
            "OCR engine not available on this build "
            f"(expected at {tesseract_path})."
        )
    # Guard the language token so a malformed config value can never be
    # misinterpreted by the engine; Tesseract expects a short script code.
    if not _OCR_LANGUAGE_RE.match(language or ""):
        raise OcrError(f"unsupported OCR language code: {language!r}")

    best = OcrResult("", 0.0)
    best_score = -1.0
    env = {**os.environ, "TESSDATA_PREFIX": str(tessdata_path)}

    # Reserve a unique temp path for Tesseract's TSV output. We create the file
    # up front so a single finally block can guarantee cleanup on every exit
    # path (including OCR timeout, which used to leak the file).
    tsv_fd, tsv_name = tempfile.mkstemp(suffix=".tsv")
    os.close(tsv_fd)
    tsv_path = Path(tsv_name)
    out_base = tsv_path.with_suffix("")  # tesseract writes "<base>.tsv"
    try:
        for psm in _PSM_CANDIDATES:
            try:
                cmd = [
                    str(tesseract),
                    str(image_path),
                    str(out_base),
                    "-l",
                    language,
                    "--psm",
                    str(psm),
                    "tsv",
                ]
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=_OCR_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                raise OcrError("OCR timed out on this image.") from exc
            except (OSError, ValueError) as exc:
                raise OcrError(f"OCR failed to start: {exc}") from exc

            try:
                if proc.returncode != 0:
                    # Non-fatal for this PSM: try the next one.
                    continue
                tsv_text = tsv_path.read_text(encoding="utf-8", errors="replace")
                text, conf = _parse_tsv(tsv_text)
            except (OSError, ValueError):
                continue

            # Score blends amount of text with confidence so a confident,
            # longer reading beats a high-confidence but empty one.
            score = len(text) * (0.01 + conf / 100.0)
            if score > best_score:
                best_score = score
                best = OcrResult(text, conf)
    finally:
        try:
            tsv_path.unlink()
        except OSError:
            pass

    if not best.text.strip():
        # Engine ran but read nothing; not an error, just nothing useful.
        return OcrResult("", best.confidence)
    return best
