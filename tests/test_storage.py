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
