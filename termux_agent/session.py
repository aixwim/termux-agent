"""Session persistence to JSONL in ~/.termux-agent/sessions/."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from termux_agent.config import CONFIG_DIR

SESSIONS_DIR = CONFIG_DIR / "sessions"
NOTES_FILE = CONFIG_DIR / "notes.json"
_NOTES_LOCK = threading.RLock()
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def validate_session_id(session_id: object) -> str:
    """Return a safe session id or reject path separators and special paths."""
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(
        session_id
    ):
        raise ValueError(
            "invalid session id (use 1-128 letters, numbers, '.', '_' or '-')"
        )
    return session_id


def _write_notes(notes: dict[str, str]) -> None:
    from termux_agent.storage import atomic_write_text

    atomic_write_text(
        NOTES_FILE,
        json.dumps(notes, ensure_ascii=False, indent=2),
    )


def all_notes() -> dict[str, str]:
    """Return a mapping of session id -> note text."""
    if not NOTES_FILE.is_file():
        return {}
    try:
        data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()} if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get_note(session_id: str) -> str | None:
    return all_notes().get(session_id)


def set_note(session_id: str, text: str) -> None:
    with _NOTES_LOCK:
        notes = all_notes()
        if text.strip():
            notes[session_id] = text.strip()
        else:
            notes.pop(session_id, None)
        _write_notes(notes)


def clear_note(session_id: str) -> bool:
    with _NOTES_LOCK:
        notes = all_notes()
        if session_id not in notes:
            return False
        notes.pop(session_id, None)
        _write_notes(notes)
        return True


def prune_notes(valid_ids: set[str]) -> int:
    """Drop notes for sessions that no longer exist. Returns number removed."""
    with _NOTES_LOCK:
        notes = all_notes()
        gone = [sid for sid in notes if sid not in valid_ids]
        if not gone:
            return 0
        for sid in gone:
            notes.pop(sid, None)
        _write_notes(notes)
        return len(gone)


class Session:
    def __init__(self, session_id: str | None = None, provider_name: str = "openai", model: str = "") -> None:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        sid = (
            validate_session_id(session_id)
            if session_id
            else time.strftime("%Y%m%d-%H%M%S")
        )
        if not session_id:
            i = 1
            while (SESSIONS_DIR / f"{sid}.jsonl").exists():
                sid = f"{time.strftime('%Y%m%d-%H%M%S')}-{i}"
                i += 1
        self.session_id = sid
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
    def modified(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return -1

    return sorted(SESSIONS_DIR.glob("*.jsonl"), key=modified, reverse=True)


def read_session(path: Path) -> list[dict]:
    out = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def session_meta(path: Path) -> tuple[int, str, dict]:
    """Fast listing metadata: (message_count, first_user_text, info).

    Parses only enough records to find the first user message and the
    provider/model info, instead of decoding the whole file. Message count
    is just the line count (each record is one line)."""
    count = 0
    first_user = ""
    info: dict = {}
    parsed = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            count += 1
            if not line.strip() or parsed >= 200:
                continue
            parsed += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not info and rec.get("provider"):
                info = {"provider": rec.get("provider", ""), "model": rec.get("model", "")}
            if not first_user and rec.get("role") == "user":
                content = rec.get("content")
                if isinstance(content, str) and content.strip():
                    first_user = content
    return count, first_user, info


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


def record_messages(messages: list[dict], provider_name: str, model: str, session_id: str | None = None) -> str:
    """Persist a finished conversation (user/assistant turns) and return its id."""
    s = Session(session_id=session_id, provider_name=provider_name, model=model)
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            s.append({"role": role, "content": content})
    return s.session_id


def resolve_session(ref: str | None = None) -> Path | None:
    """Resolve a session ref (id prefix or 'latest') to a path."""
    if ref and ref not in ("latest", ""):
        matches = [s for s in list_sessions() if s.stem.startswith(ref)]
        return matches[-1] if matches else None
    return latest_session()


def delete_session(ref: str | None = None) -> Path | None:
    """Delete a session by id prefix (or the latest when ref is None/latest)."""
    path = resolve_session(ref)
    if not path:
        return None
    try:
        path.unlink()
    except OSError:
        return None
    return path


def export_session(ref: str | None = None) -> dict:
    """Export a session as a portable dict (for backup / moving devices)."""
    path = resolve_session(ref)
    if not path:
        raise FileNotFoundError("session not found")
    recs = read_session(path)
    info = next((r for r in recs if r.get("provider")), {})
    return {
        "version": 1,
        "id": path.stem,
        "provider": info.get("provider") or "",
        "model": info.get("model") or "",
        "messages": [
            {"role": r["role"], "content": r["content"]}
            for r in recs
            if r.get("role") in ("user", "assistant") and isinstance(r.get("content"), str) and r["content"].strip()
        ],
    }


def import_session(data: dict, session_id: str | None = None) -> str:
    """Import a portable session dict; returns the (reused) session id.

    If the target id already exists it is replaced, so a restore is exact.
    """
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("missing 'messages'")
    clean = [
        {"role": m["role"], "content": str(m["content"])}
        for m in messages
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if not clean:
        raise ValueError("no user/assistant messages")
    s = Session(
        session_id=session_id or data.get("id"),
        provider_name=str(data.get("provider") or ""),
        model=str(data.get("model") or ""),
    )
    now = time.time()
    records = []
    for offset, message in enumerate(clean):
        record = {
            "ts": now + offset * 0.000001,
            "provider": s.provider_name,
            "model": s.model,
            **message,
        }
        records.append(json.dumps(record, ensure_ascii=False))
    from termux_agent.storage import atomic_write_text

    atomic_write_text(s.path, "\n".join(records) + "\n")
    return s.session_id


def prune_sessions(keep: int) -> int:
    """Delete all but the `keep` newest sessions. Returns number deleted."""
    sessions = list_sessions()
    removed = 0
    for path in sessions[keep:]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def prune_days(days: int) -> int:
    """Delete sessions older than `days` days. Returns number deleted."""
    import time

    cutoff = time.time() - days * 86400
    removed = 0
    for path in list_sessions():
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed
