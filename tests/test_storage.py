from __future__ import annotations

import threading

import pytest

from termux_agent.storage import atomic_write_text


def test_atomic_write_text_replaces_content_without_temp_files(tmp_path):
    target = tmp_path / "notes.json"
    target.write_text("old")

    atomic_write_text(target, "new")

    assert target.read_text() == "new"
    assert list(tmp_path.glob(".notes.json.*.tmp")) == []


def test_atomic_write_failure_preserves_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "notes.json"
    target.write_text("old")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(target, "new")

    assert target.read_text() == "old"
    assert list(tmp_path.glob(".notes.json.*.tmp")) == []


def test_concurrent_note_updates_do_not_overwrite_each_other(tmp_path, monkeypatch):
    from termux_agent import session

    monkeypatch.setattr(session, "NOTES_FILE", tmp_path / "notes.json")
    threads = [
        threading.Thread(target=session.set_note, args=(f"session-{i}", f"note-{i}"))
        for i in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert session.all_notes() == {
        f"session-{i}": f"note-{i}" for i in range(20)
    }


@pytest.mark.parametrize(
    "session_id",
    ["../escape", "../../escape", "/absolute", "has/slash", "", "x" * 129],
)
def test_session_rejects_unsafe_ids(tmp_path, monkeypatch, session_id):
    from termux_agent import session

    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path / "sessions")
    with pytest.raises(ValueError, match="invalid session id"):
        session.validate_session_id(session_id)


def test_import_session_rejects_path_traversal(tmp_path, monkeypatch):
    from termux_agent import session

    sessions = tmp_path / "sessions"
    monkeypatch.setattr(session, "SESSIONS_DIR", sessions)
    data = {
        "id": "../../escaped",
        "messages": [{"role": "user", "content": "hello"}],
    }

    with pytest.raises(ValueError, match="invalid session id"):
        session.import_session(data)

    assert not (tmp_path.parent / "escaped.jsonl").exists()


def test_import_session_replaces_existing_file_atomically(tmp_path, monkeypatch):
    from termux_agent import session

    sessions = tmp_path / "sessions"
    monkeypatch.setattr(session, "SESSIONS_DIR", sessions)
    existing = sessions / "safe-id.jsonl"
    sessions.mkdir()
    existing.write_text("original\n")
    data = {
        "id": "safe-id",
        "provider": "test",
        "model": "m",
        "messages": [{"role": "user", "content": "replacement"}],
    }

    session.import_session(data)

    records = session.read_session(existing)
    assert len(records) == 1
    assert records[0]["content"] == "replacement"


def test_failed_session_import_preserves_existing_file(tmp_path, monkeypatch):
    from termux_agent import session

    sessions = tmp_path / "sessions"
    monkeypatch.setattr(session, "SESSIONS_DIR", sessions)
    existing = sessions / "safe-id.jsonl"
    sessions.mkdir()
    existing.write_text("original\n")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        session.import_session(
            {
                "id": "safe-id",
                "messages": [{"role": "user", "content": "replacement"}],
            }
        )

    assert existing.read_text() == "original\n"
    assert list(sessions.glob(".safe-id.jsonl.*.tmp")) == []
