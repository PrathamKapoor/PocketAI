"""Image validation, normalization, and the OCR -> context pipeline.

An image is an *input modality*, not a document: it is validated, downscaled
to a memory-safe size, OCR'd offline, and turned into text the supervisor can
reason about. No image bytes are ever kept in RAM longer than needed and no
permanent copy is made — the normalized temp file is deleted as soon as OCR is
done.
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageFile

from backend.config.loader import ImageLimits, PocketAIConfig
from backend.image.errors import (
    ImageCorrupt,
    ImageTempError,
    ImageTooLarge,
    UnsupportedImageType,
)
from backend.image.ocr import run_ocr

# Map a sniffed Pillow format to the canonical MIME we report.
_PIL_FORMAT_TO_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
}


@dataclass
class ImageInput:
    """The result of turning a pasted/uploaded image into model context."""

    # Raw OCR text extracted from the image (may be empty when unreadable).
    ocr_text: str
    # Mean word confidence 0-100, or None when OCR did not run.
    ocr_confidence: float | None
    ocr_available: bool
    ocr_error: str | None
    # Detected/normalized MIME, e.g. "image/png".
    mime: str
    width: int
    height: int
    # Original file name (sanitized) or None.
    name: str | None
    # Text block injected into the user turn so the model sees the OCR text.
    context_text: str
    # Compact metadata stored alongside the user message (no image bytes).
    attachment: dict = field(default_factory=dict)


def _decode_base64(data: str) -> bytes:
    # Tolerate an accidental data-URL prefix from the frontend.
    if "," in data and data[:40].lower().startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageCorrupt("the image data is not valid base64") from exc


def _sanitize_name(name: str | None) -> str | None:
    if not name:
        return None
    # Keep the basename only; strip any path separators (path-traversal guard).
    base = os.path.basename(name)
    return base[:120] if base else None


def _validate_mime(declared: str | None, sniffed: str | None, cfg: ImageLimits) -> str:
    """Resolve a canonical allowed MIME from the declared type and the sniff."""
    allowed = set(cfg.allowed_mime)
    if sniffed in _PIL_FORMAT_TO_MIME:
        sniffed_mime = _PIL_FORMAT_TO_MIME[sniffed]
        if sniffed_mime in allowed:
            return sniffed_mime
    # Fall back to a declared type (e.g. clipboard sometimes omits it).
    if declared and declared.lower() in allowed:
        return declared.lower()
    raise UnsupportedImageType(
        "unsupported image type"
        + (f" ({declared or sniffed or 'unknown'})" if (declared or sniffed) else "")
        + f"; supported: {', '.join(sorted(allowed))}"
    )


def _normalize_to_temp(raw: bytes, cfg: ImageLimits, temp_dir: Path) -> Path:
    """Decode, validate, downscale and save a PNG; return its temp path.

    Raises ImageCorrupt / ImageTooLarge on bad input. The returned file must be
    deleted by the caller once OCR is finished.
    """
    try:
        Image.MAX_IMAGE_PIXELS = cfg.max_pixels
        img = Image.open(io.BytesIO(raw))
        img.load()  # force full decode so truncation is caught now
    except (Image.UnidentifiedImageError, OSError, ValueError, MemoryError, Image.DecompressionBombError) as exc:
        raise ImageCorrupt("the file is not a readable image") from exc

    width, height = img.size
    if max(width, height) < cfg.min_dimension:
        raise ImageCorrupt("the image is too small to be useful")
    if max(width, height) > cfg.max_dimension:
        # Downscale in place (LANCZOS keeps text crisp) to bound memory/OCR.
        img.thumbnail((cfg.max_dimension, cfg.max_dimension), Image.LANCZOS)
        width, height = img.size

    # Small screenshots often have tiny text; upscaling to a floor before OCR
    # markedly improves Tesseract accuracy, and the footprint stays bounded
    # (~1200px on the long side -> a few MB of RGB pixels).
    ocr_floor = 1200
    long_side = max(width, height)
    if long_side < ocr_floor:
        scale = ocr_floor / float(long_side)
        img = img.resize(
            (int(width * scale), int(height * scale)), Image.LANCZOS
        )
        width, height = img.size

    # Normalize to RGB so alpha/palette quirks never confuse OCR or the model.
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            "wb", delete=False, suffix=".png", dir=str(temp_dir)
        )
    except OSError as exc:
        raise ImageTempError(f"could not create a temporary image: {exc}") from exc

    try:
        img.save(tmp, format="PNG")
    except (OSError, ValueError) as exc:
        tmp.close()
        _safe_unlink(tmp.name)
        raise ImageCorrupt("the image could not be processed") from exc
    finally:
        tmp.close()

    return Path(tmp.name)


def _safe_unlink(path: str | Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def process_image(
    base64_str: str,
    name: str | None,
    declared_type: str | None,
    cfg: PocketAIConfig,
) -> ImageInput:
    """Full image pipeline: decode -> validate -> normalize -> OCR -> context.

    OCR failures degrade gracefully (ocr_available=False) so the chat can still
    proceed with a note that the image could not be read. Image-level failures
    (bad type, too large, corrupt) raise ImageError and abort the request.
    """
    raw = _decode_base64(base64_str)

    max_bytes = cfg.image.max_upload_mb * 1024 * 1024 + 1
    if len(raw) > max_bytes:
        raise ImageTooLarge(
            f"image is {len(raw)//1024} KB; the limit is "
            f"{cfg.image.max_upload_mb} MB"
        )

    # Sniff the real format before trusting the declared one.
    try:
        Image.MAX_IMAGE_PIXELS = cfg.image.max_pixels
        sniffed = Image.open(io.BytesIO(raw)).format
    except (Image.UnidentifiedImageError, OSError, ValueError, MemoryError, Image.DecompressionBombError):
        sniffed = None
    mime = _validate_mime(declared_type, sniffed, cfg.image)

    temp_path = _normalize_to_temp(raw, cfg.image, cfg.image_temp_dir)
    try:
        if cfg.image.ocr_enabled:
            try:
                result = run_ocr(
                    temp_path,
                    tesseract_path=cfg.tesseract_path,
                    tessdata_path=cfg.tessdata_path,
                    language=cfg.image.ocr_language,
                )
                ocr_text = result.text.strip()
                ocr_conf = round(result.confidence, 1) if ocr_text else None
                ocr_available = bool(ocr_text)
                ocr_error = None
            except Exception as exc:  # graceful degradation, never crash the chat
                ocr_text = ""
                ocr_conf = None
                ocr_available = False
                ocr_error = str(exc)
        else:
            ocr_text = ""
            ocr_conf = None
            ocr_available = False
            ocr_error = "OCR is disabled in configuration"
    finally:
        _safe_unlink(temp_path)

    width, height = _peek_dimensions(raw)

    clean_name = _sanitize_name(name)
    context, attachment = _build_context_and_attachment(
        mime=mime,
        width=width,
        height=height,
        name=clean_name,
        ocr_text=ocr_text,
        ocr_conf=ocr_conf,
        ocr_available=ocr_available,
        ocr_error=ocr_error,
    )
    return ImageInput(
        ocr_text=ocr_text,
        ocr_confidence=ocr_conf,
        ocr_available=ocr_available,
        ocr_error=ocr_error,
        mime=mime,
        width=width,
        height=height,
        name=clean_name,
        context_text=context,
        attachment=attachment,
    )


def _peek_dimensions(raw: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(raw)) as img:
            return img.size
    except (OSError, ValueError):
        return (0, 0)


def _build_context_and_attachment(
    *,
    mime: str,
    width: int,
    height: int,
    name: str | None,
    ocr_text: str,
    ocr_conf: float | None,
    ocr_available: bool,
    ocr_error: str | None,
) -> tuple[str, dict]:
    label = mime.split("/")[-1].upper()
    if name:
        head = f"[Image attached: {name}]"
    else:
        head = f"[Image attached: {label}]"

    if ocr_available:
        conf_note = f"confidence: {ocr_conf:.0f}%" if ocr_conf else "confidence: n/a"
        context = (
            f"{head} Text read from the image by offline OCR ({conf_note}):\n\n"
            f"{ocr_text}\n\n"
            "[End of image OCR text.]"
        )
    else:
        context = (
            f"{head} I couldn't read enough text from this image."
            + " Try a clearer or higher-resolution screenshot."
            + " PocketAI can read text from images but cannot interpret"
            " diagrams, photos or visual layout."
        )

    attachment = {
        "type": "image",
        "mime": mime,
        "name": name,
        "width": width,
        "height": height,
        "ocr_available": ocr_available,
        "ocr_confidence": ocr_conf,
    }
    return context, attachment


def combine_user_message(message: str, image: ImageInput | None) -> str:
    """Join the typed question with the OCR context for the model/user turn."""
    if not image:
        return message
    if message:
        return f"{message}\n\n{image.context_text}"
    return image.context_text


def image_system_note() -> str:
    """A short instruction appended to the system prompt for image requests."""
    return (
        "The user attached an image. The user message below contains text that"
        " was extracted from that image by offline OCR; the model cannot see the"
        " pixels. Answer using the extracted text. If no text was extracted, say"
        " plainly that you can read text from images via OCR but cannot analyze"
        " diagrams, photos or visual layout, and ask the user to describe what"
        " they need."
    )
