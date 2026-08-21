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


def test_restore_rejects_malformed_manifest(tmp_path, monkeypatch):
    import io

    from termux_agent import cli

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text("not-json")
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    assert cli.cmd_restore(str(bundle)) == 1
    assert "Invalid bundle manifest" in output.getvalue()


def test_restore_rejects_foreign_manifest(tmp_path, monkeypatch):
    import io

    from termux_agent import cli

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text('{"app":"other"}')
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    assert cli.cmd_restore(str(bundle)) == 1
    assert "expected app 'termux-agent'" in output.getvalue()


def test_restore_preflight_prevents_partial_write(tmp_path, monkeypatch):
    import io

    from termux_agent import cli, session

    bundle = tmp_path / "bundle"
    sessions = bundle / "sessions"
    sessions.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        '{"app":"termux-agent","version":"test"}'
    )
    (bundle / "config.yaml").write_text("provider: test\n")
    (sessions / ".unsafe.jsonl").write_text(
        '{"role":"user","content":"hello"}\n'
    )
    destination = tmp_path / "destination"
    monkeypatch.setattr(cli, "CONFIG_DIR", destination)
    monkeypatch.setattr(cli, "CONFIG_FILE", destination / "config.yaml")
    monkeypatch.setattr(session, "SESSIONS_DIR", destination / "sessions")
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    assert cli.cmd_restore(str(bundle)) == 1
    assert "Invalid session file" in output.getvalue()
    assert not destination.exists()


def _tar_bytes(entries):
    import io
    import tarfile

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = content.decode()
                archive.addfile(info)
    return output.getvalue()


def test_bundle_archive_rejects_extracted_size_limit(tmp_path):
    from termux_agent.cli import _extract_bundle_archive

    raw = _tar_bytes([("large.txt", b"x" * 20, "file")])
    with pytest.raises(ValueError, match="expands beyond"):
        _extract_bundle_archive(raw, tmp_path, max_extracted_bytes=10)

    assert list(tmp_path.iterdir()) == []


def test_bundle_archive_rejects_too_many_entries(tmp_path):
    from termux_agent.cli import _extract_bundle_archive

    raw = _tar_bytes(
        [("one.txt", b"1", "file"), ("two.txt", b"2", "file")]
    )
    with pytest.raises(ValueError, match="entry limit"):
        _extract_bundle_archive(raw, tmp_path, max_entries=1)

    assert list(tmp_path.iterdir()) == []


def test_bundle_archive_rejects_symlinks(tmp_path):
    from termux_agent.cli import _extract_bundle_archive

    raw = _tar_bytes([("link", b"/data/data", "symlink")])
    with pytest.raises(ValueError, match="unsupported entry"):
        _extract_bundle_archive(raw, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_bundle_archive_preflight_rejects_traversal_without_partial_write(
    tmp_path,
):
    import tarfile

    from termux_agent.cli import _extract_bundle_archive

    raw = _tar_bytes(
        [
            ("safe.txt", b"safe", "file"),
            ("../escape.txt", b"escape", "file"),
        ]
    )
    with pytest.raises(tarfile.FilterError):
        _extract_bundle_archive(raw, tmp_path)

    assert list(tmp_path.iterdir()) == []
    assert not (tmp_path.parent / "escape.txt").exists()
