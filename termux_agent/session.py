"""Session persistence to JSONL in ~/.termux-agent/sessions/."""
from __future__ import annotations

import json
import time
from pathlib import Path

from termux_agent.config import CONFIG_DIR

SESSIONS_DIR = CONFIG_DIR / "sessions"


class Session:
    def __init__(self, session_id: str | None = None, provider_name: str = "openai", model: str = "") -> None:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or time.strftime("%Y%m%d-%H%M%S")
        self.provider_name = provider_name
        self.model = model
        self.path = SESSIONS_DIR / f"{self.session_id}.jsonl"

    def append(self, entry: dict) -> None:
        record = {
            "ts": time.time(),
            "provider": self.provider_name,
            "model": self.model,
        }
        record.update(entry)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def list_sessions() -> list[Path]:
    if not SESSIONS_DIR.exists():
        return []
    return sorted(SESSIONS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def read_session(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def session_messages(path: Path) -> list[dict]:
    """Rebuild the conversation history (user/assistant) from a session file."""
    msgs = []
    for rec in read_session(path):
        if rec.get("role") in ("user", "assistant"):
            content = rec.get("content")
            if isinstance(content, str) and content.strip():
                msgs.append({"role": rec["role"], "content": content})
    return msgs


def latest_session() -> Path | None:
    sessions = list_sessions()
    return sessions[0] if sessions else None


def delete_session(ref: str | None = None) -> Path | None:
    """Delete a session by id prefix (or the latest when ref is None/latest)."""
    if ref and ref not in ("latest", ""):
        matches = [s for s in list_sessions() if s.stem.startswith(ref)]
        path = matches[-1] if matches else None
    else:
        path = latest_session()
    if not path:
        return None
    try:
        path.unlink()
    except OSError:
        return None
    return path