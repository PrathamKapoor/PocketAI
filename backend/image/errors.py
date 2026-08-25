"""Image-input errors.

Image input is a distinct input modality (not a document). Every failure the
image pipeline can raise maps to a clean HTTP status so the backend can return
a friendly JSON error instead of a Python traceback.
"""

from __future__ import annotations


class ImageError(RuntimeError):
    """Base class for image-input failures (HTTP 400 by default)."""

    status_code = 400

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class UnsupportedImageType(ImageError):
    """The clipboard/file held something that is not an allowed image."""

    status_code = 415


class ImageTooLarge(ImageError):
    """The image exceeds the configured byte or dimension budget."""

    status_code = 413


class ImageCorrupt(ImageError):
    """The bytes are not a decodable image (truncated, malformed, garbage)."""

    status_code = 422


class OcrError(ImageError):
    """OCR could not run (engine missing, crashed, or produced no output)."""

    status_code = 502


class ImageTempError(ImageError):
    """A temporary file could not be created or cleaned up."""

    status_code = 500
