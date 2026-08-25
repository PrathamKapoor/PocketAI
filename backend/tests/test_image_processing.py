"""Image-processing pipeline tests: validation, normalization, OCR, cleanup.

Real bundled Tesseract is used where possible so OCR success is genuine, not
mocked. Failures are exercised by shrinking config limits or feeding bad input.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image, ImageDraw

from backend.image import process_image
from backend.image.errors import (
    ImageCorrupt,
    ImageTooLarge,
    UnsupportedImageType,
)


def _png_bytes(text: str = "Hello PocketAI", size=(800, 240)) -> bytes:
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).text((10, 10), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _jpg_bytes() -> bytes:
    img = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(img).text((10, 10), "JPEG test 123", fill="black")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ---- valid inputs ---------------------------------------------------------

def test_valid_png_is_ocr_read(config):
    res = process_image(_b64(_png_bytes("Solve 2x plus 3")), "shot.png", "image/png", config)
    assert res.mime == "image/png"
    assert res.ocr_available is True
    assert "Solve" in res.ocr_text or "2x" in res.ocr_text
    assert res.ocr_confidence is not None
    assert res.attachment["type"] == "image"
    assert res.attachment["ocr_available"] is True


def test_valid_jpeg_accepted(config):
    res = process_image(_b64(_jpg_bytes()), "photo.jpg", "image/jpeg", config)
    assert res.mime == "image/jpeg"
    assert res.width == 300 and res.height == 300


def test_oversized_image_is_downscaled_not_rejected(config):
    # 5000x5000 far exceeds max_dimension (2400); must downscale, not error.
    big = Image.new("RGB", (5000, 5000), "white")
    ImageDraw.Draw(big).text((50, 50), "Big diagram label", fill="black")
    raw = io.BytesIO()
    big.save(raw, "PNG")
    res = process_image(_b64(raw.getvalue()), "big.png", "image/png", config)
    # No exception: the huge image was downscaled and processed, not rejected.
    assert res.mime == "image/png"


def test_normalize_downscales_large_image(config):
    from backend.image.processing import _normalize_to_temp

    big = Image.new("RGB", (5000, 5000), "white")
    raw = io.BytesIO()
    big.save(raw, "PNG")
    path = _normalize_to_temp(raw.getvalue(), config.image, config.image_temp_dir)
    try:
        with Image.open(path) as im:
            assert max(im.size) <= config.image.max_dimension
    finally:
        path.unlink()


# ---- invalid inputs -------------------------------------------------------

def test_invalid_mime_rejected(config):
    cfg = config
    cfg.image.allowed_mime = []  # nothing allowed -> force rejection
    with pytest.raises(UnsupportedImageType):
        process_image(_b64(_png_bytes()), "x.png", "image/png", cfg)


def test_corrupt_bytes_rejected(config):
    with pytest.raises(ImageCorrupt):
        process_image(base64.b64encode(b"this is not an image").decode(), "x.png", "image/png", config)


def test_oversized_bytes_rejected(config):
    cfg = config
    cfg.image.max_upload_mb = 0  # any non-empty image exceeds the limit
    with pytest.raises(ImageTooLarge):
        process_image(_b64(_png_bytes()), "x.png", "image/png", cfg)


def test_decompression_bomb_rejected(config):
    cfg = config
    cfg.image.max_pixels = 100  # tiny pixel budget -> large image rejected
    small = Image.new("RGB", (500, 500), "white")
    raw = io.BytesIO()
    small.save(raw, "PNG")
    with pytest.raises(ImageCorrupt):
        process_image(_b64(raw.getvalue()), "x.png", "image/png", cfg)


def test_too_small_image_rejected(config):
    tiny = Image.new("RGB", (4, 4), "white")
    raw = io.BytesIO()
    tiny.save(raw, "PNG")
    with pytest.raises(ImageCorrupt):
        process_image(_b64(raw.getvalue()), "x.png", "image/png", config)


# ---- OCR behavior ---------------------------------------------------------

def test_ocr_failure_degrades_gracefully(config):
    cfg = config
    cfg.image.tesseract_relative = "runtime/ocr/tesseract-MISSING.exe"
    res = process_image(_b64(_png_bytes()), "x.png", "image/png", cfg)
    assert res.ocr_available is False
    assert res.ocr_error is not None
    # The request can still proceed; the context explains OCR failed.
    assert "couldn't read" in res.context_text.lower() or "could not read" in res.context_text.lower()


def test_ocr_disabled_flag(config):
    cfg = config
    cfg.image.ocr_enabled = False
    res = process_image(_b64(_png_bytes()), "x.png", "image/png", cfg)
    assert res.ocr_available is False
    assert res.ocr_text == ""


# ---- temp-file hygiene ----------------------------------------------------

def test_temp_image_file_is_cleaned_up(config):
    import glob
    import tempfile

    tmp_dir = config.image_temp_dir
    before = set(glob.glob(str(tmp_dir / "*.png"))) if tmp_dir.exists() else set()
    process_image(_b64(_png_bytes()), "x.png", "image/png", config)
    after = set(glob.glob(str(tmp_dir / "*.png")))
    # No normalized .png should remain after OCR completes.
    leftover = after - before
    assert not leftover, f"leftover temp images: {leftover}"


def test_ocr_timeout_does_not_leave_temp_tsv(config, monkeypatch, tmp_path):
    import subprocess as _sp

    import backend.image.ocr as ocr_mod

    def _boom(*a, **k):
        raise _sp.TimeoutExpired("tesseract", 60)

    monkeypatch.setattr(ocr_mod.subprocess, "run", _boom)
    # Route the temp TSV into a directory we can inspect afterwards.
    import tempfile as _tf

    orig_mkstemp = _tf.mkstemp  # capture before patching
    monkeypatch.setattr(
        ocr_mod.tempfile,
        "mkstemp",
        lambda suffix=".tsv": orig_mkstemp(suffix=suffix, dir=str(tmp_path)),
    )
    img = tmp_path / "img.png"
    img.write_bytes(_png_bytes())
    with pytest.raises(ocr_mod.OcrError):
        ocr_mod.run_ocr(
            img,
            tesseract_path="runtime/ocr/tesseract.exe",
            tessdata_path="runtime/ocr/tessdata",
            language="eng",
        )
    # The TSV must be removed even though OCR timed out (regression: leak).
    assert not list(tmp_path.glob("*.tsv")), "OCR timeout leaked a temp TSV file"


def test_ocr_rejects_invalid_language(config, monkeypatch):
    import backend.image.ocr as ocr_mod

    # Bypass the engine-existence check so we exercise only the language guard.
    monkeypatch.setattr(ocr_mod.Path, "is_file", lambda self: True)
    with pytest.raises(ocr_mod.OcrError):
        ocr_mod.run_ocr(
            "img.png",
            tesseract_path="runtime/ocr/tesseract.exe",
            tessdata_path="runtime/ocr/tessdata",
            language="eng; rm -rf",
        )


def test_memory_error_during_decode_is_image_corrupt(config, monkeypatch):
    import backend.image.processing as proc_mod
    from PIL import Image

    def _boom(*a, **k):
        raise MemoryError("simulated OOM on low-RAM machine")

    monkeypatch.setattr(Image, "open", _boom)
    from backend.image.errors import ImageCorrupt

    with pytest.raises(ImageCorrupt):
        process_image(_b64(_png_bytes()), "x.png", "image/png", config)



# ---- message combine helper ---------------------------------------------

def test_combine_user_message_with_and_without_image():
    from backend.image import combine_user_message

    class FakeImg:
        context_text = "[OCR block]"

    assert combine_user_message("Explain this", FakeImg()) == "Explain this\n\n[OCR block]"
    assert combine_user_message("", FakeImg()) == "[OCR block]"
    assert combine_user_message("just text", None) == "just text"
