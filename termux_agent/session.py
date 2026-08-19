"""Persistensi sesi ke JSONL di ~/.termux-agent/sessions/."""
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