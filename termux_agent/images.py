"""Safe handling for images downloaded from remote URLs."""
from __future__ import annotations

import tempfile
import base64
import binascii
import urllib.request
from pathlib import Path

MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ImageDownloadError(ValueError):
    """Raised when a remote image cannot be downloaded safely."""


def _image_suffix(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def read_image(path: str | Path, *, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
    """Read a bounded local image and return its bytes and detected MIME type."""
    image_path = Path(path).expanduser()
    if not image_path.is_file():
        raise ImageDownloadError(f"image not found: {image_path}")
    try:
        with image_path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        raise ImageDownloadError(f"cannot read image: {exc}") from exc
    if len(data) > max_bytes:
        raise ImageDownloadError(
            f"image exceeds the {max_bytes // (1024 * 1024)} MiB limit"
        )
    suffix = _image_suffix(data)
    if suffix is None:
        raise ImageDownloadError(
            "file is not a supported PNG, JPEG, GIF, or WebP image"
        )
    return data, IMAGE_MIME_TYPES[suffix]


def save_image_bytes(data: bytes, *, max_bytes: int = MAX_IMAGE_BYTES) -> Path:
    """Validate image bytes and store them in a unique temporary file."""
    if len(data) > max_bytes:
        raise ImageDownloadError(
            f"image exceeds the {max_bytes // (1024 * 1024)} MiB limit"
        )
    suffix = _image_suffix(data)
    if suffix is None:
        raise ImageDownloadError(
            "data is not a supported PNG, JPEG, GIF, or WebP image"
        )
    tmp = tempfile.NamedTemporaryFile(
        prefix="termux-agent-img-", suffix=suffix, delete=False
    )
    try:
        tmp.write(data)
        tmp.close()
    except Exception:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise
    return Path(tmp.name)


def decode_data_image(uri: str, *, max_bytes: int = MAX_IMAGE_BYTES) -> Path:
    """Decode a bounded base64 image data URI into a temporary file."""
    header, separator, encoded = uri.partition(",")
    if not separator or not header.lower().startswith("data:image/") or ";base64" not in header.lower():
        raise ImageDownloadError("invalid base64 image data URI")
    if len(encoded) > ((max_bytes + 2) // 3) * 4 + 4:
        raise ImageDownloadError(
            f"image exceeds the {max_bytes // (1024 * 1024)} MiB limit"
        )
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageDownloadError("invalid base64 image data URI") from exc
    return save_image_bytes(data, max_bytes=max_bytes)


def download_image(url: str, *, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[Path, int]:
    """Download a recognized image into a unique temporary file."""
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length:
                try:
                    declared_size = int(length)
                except ValueError as exc:
                    raise ImageDownloadError("invalid Content-Length from image server") from exc
                if declared_size > max_bytes:
                    raise ImageDownloadError(f"image exceeds the {max_bytes // (1024 * 1024)} MiB limit")
            data = response.read(max_bytes + 1)
    except ImageDownloadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImageDownloadError(str(exc)) from exc

    if len(data) > max_bytes:
        raise ImageDownloadError(f"image exceeds the {max_bytes // (1024 * 1024)} MiB limit")
    try:
        path = save_image_bytes(data, max_bytes=max_bytes)
    except ImageDownloadError as exc:
        raise ImageDownloadError(
            "response is not a supported PNG, JPEG, GIF, or WebP image"
        ) from exc
    return path, len(data)


def cleanup_downloaded_image(path: Path | None) -> None:
    if path is not None:
        path.unlink(missing_ok=True)
