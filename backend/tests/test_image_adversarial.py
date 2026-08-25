"""Adversarial image-input tests (audit section 9).

Exercises malformed, hostile, and edge-case images through process_image to
ensure they never crash the backend, never leak temp files, and never reach
the model with garbage. OCR uses the real bundled Tesseract where the image is
actually decodable; decode failures are rejected before OCR runs.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image, ImageDraw

from backend.image import process_image
from backend.image.errors import ImageCorrupt, ImageTooLarge, UnsupportedImageType
from backend.image.processing import _normalize_to_temp


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _png(text="hello", size=(200, 80)) -> bytes:
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).text((10, 10), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_zero_byte_image(config):
    with pytest.raises(ImageCorrupt):
        process_image(_b64(b""), "x.png", "image/png", config)


def test_corrupt_png(config):
    with pytest.raises(ImageCorrupt):
        process_image(_b64(b"\x89PNG\r\n\x1a\ncorrupted trailing bytes here"), "x.png", "image/png", config)


def test_corrupt_jpeg(config):
    with pytest.raises(ImageCorrupt):
        process_image(_b64(b"\xff\xd8\xff\xe0not a real jpeg"), "x.jpg", "image/jpeg", config)


def test_renamed_non_image_with_image_mime(config):
    with pytest.raises(ImageCorrupt):
        process_image(_b64(b"just some plain text, not an image"), "x.png", "image/png", config)


def test_huge_dimensions_rejected_as_bomb(config):
    png_sig = b"\x89PNG\r\n\x1a\n"
    ihdr = b"IHDR" + (50000).to_bytes(4, "big") + (50000).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    chunk = (13).to_bytes(4, "big") + ihdr + b"\x00\x00\x00\x00"
    huge = png_sig + chunk
    with pytest.raises(ImageCorrupt):
        process_image(_b64(huge), "big.png", "image/png", config)


def test_tiny_image_rejected(config):
    tiny = Image.new("RGB", (4, 4), "white")
    buf = io.BytesIO()
    tiny.save(buf, "PNG")
    with pytest.raises(ImageCorrupt):
        process_image(_b64(buf.getvalue()), "tiny.png", "image/png", config)


def test_extremely_wide_image_accepted(config):
    wide = Image.new("RGB", (5000, 2000), "white")
    ImageDraw.Draw(wide).text((50, 50), "wide content here", fill="black")
    buf = io.BytesIO()
    wide.save(buf, "PNG")
    res = process_image(_b64(buf.getvalue()), "wide.png", "image/png", config)
    assert res.mime == "image/png"
    assert res.width == 5000 and res.height == 2000


def test_extremely_wide_image_is_downscaled_for_ocr(config):
    wide = Image.new("RGB", (5000, 2000), "white")
    ImageDraw.Draw(wide).text((50, 50), "wide content here", fill="black")
    buf = io.BytesIO()
    wide.save(buf, "PNG")
    tmp = _normalize_to_temp(buf.getvalue(), config.image, config.image_temp_dir)
    try:
        with Image.open(tmp) as im:
            w, h = im.size
    finally:
        tmp.unlink(missing_ok=True)
    assert max(w, h) <= 2400
    assert min(w, h) >= 8


def test_transparent_png_normalized_to_rgb(config):
    img = Image.new("RGBA", (120, 120), (255, 255, 255, 0))
    ImageDraw.Draw(img).text((10, 10), "alpha", fill=(0, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    res = process_image(_b64(buf.getvalue()), "a.png", "image/png", config)
    assert res.mime == "image/png"


def test_grayscale_accepted(config):
    img = Image.new("L", (120, 120), 255)
    ImageDraw.Draw(img).text((10, 10), "gray", fill=0)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    res = process_image(_b64(buf.getvalue()), "g.png", "image/png", config)
    assert res.mime == "image/png"


def test_just_over_upload_limit_rejected(config):
    cfg = config
    cfg.image.max_upload_mb = 0  # any payload >0 bytes must be rejected
    with pytest.raises(ImageTooLarge):
        process_image(_b64(_png(size=(120, 120))), "big.png", "image/png", cfg)


def test_under_upload_limit_accepted(config):
    cfg = config
    cfg.image.max_upload_mb = 1
    small = _png(size=(120, 120))
    res = process_image(_b64(small), "small.png", "image/png", cfg)
    assert res.mime == "image/png"


def test_unicode_filename_sanitized(config):
    res = process_image(_b64(_png()), "测试截图.png", "image/png", config)
    assert res.name == "测试截图.png"


def test_path_traversal_filename_neutralized(config):
    res = process_image(_b64(_png()), "../../../evil.png", "image/png", config)
    assert ".." not in (res.name or "")
    assert res.name == "evil.png"


def test_quoted_filename_kept_as_metadata_only(config):
    res = process_image(_b64(_png()), 'a"b.png', "image/png", config)
    assert res.name == 'a"b.png'


def test_unknown_mime_rejected(config):
    # Declared type is NOT trusted: content is sniffed. Junk bytes whose real
    # type cannot be detected, declared as an unsupported image/tiff, must be
    # rejected (rather than letting a bogus mime reach the model).
    with pytest.raises(UnsupportedImageType):
        process_image(_b64(b"\x00\x01\x02\x03random-junk-not-an-image"), "x.tiff", "image/tiff", config)
