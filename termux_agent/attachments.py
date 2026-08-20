"""Memory-bounded text attachment loading shared by CLI and REPL."""
from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path

ATTACH_MAX_BYTES = 2 * 1024 * 1024
ATTACH_TOTAL_MAX_BYTES = 4 * 1024 * 1024


class AttachmentError(ValueError):
    """Raised when an attachment cannot be loaded safely."""


def load_attachment(source: str) -> tuple[str, str]:
    """Return (display name, decoded text) for a local path or HTTP(S) URL."""
    if source.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(source, timeout=30) as response:
                raw = response.read(ATTACH_MAX_BYTES + 1)
        except Exception as error:  # noqa: BLE001
            raise AttachmentError(f"Cannot fetch attachment {source}: {error}") from error
        name = Path(urllib.parse.urlparse(source).path).name or "remote"
    else:
        path = Path(source).expanduser()
        try:
            if path.stat().st_size > ATTACH_MAX_BYTES:
                raise AttachmentError(f"Attachment exceeds 2 MiB limit: {path}")
            raw = path.read_bytes()
        except AttachmentError:
            raise
        except OSError as error:
            raise AttachmentError(f"Cannot read attachment {path}: {error}") from error
        name = str(path)
    if len(raw) > ATTACH_MAX_BYTES:
        raise AttachmentError(f"Attachment exceeds 2 MiB limit: {source}")
    if b"\x00" in raw[:8192]:
        raise AttachmentError(f"Attachment appears to be binary: {source}")
    return name, raw.decode("utf-8", errors="replace")


def append_attachments(prompt: str, sources: list[str], bracket: bool = False) -> str:
    """Append bounded text attachments to a prompt using a consistent envelope."""
    blocks = []
    total_bytes = 0
    for source in sources:
        name, content = load_attachment(source)
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > ATTACH_TOTAL_MAX_BYTES:
            raise AttachmentError("Attachments exceed 4 MiB combined limit")
        if bracket:
            blocks.append(f"[file: {name}]\n{content}")
        else:
            blocks.append(f"<file name={name}>\n{content}\n</file>")
    return "\n\n".join([part for part in (prompt, *blocks) if part]).strip()
