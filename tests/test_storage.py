from __future__ import annotations

import threading

import pytest

from termux_agent.storage import atomic_copy_file, atomic_write_text, sha256_file


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


def test_atomic_copy_file_streams_and_replaces_content(tmp_path):
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes((b"chunk" * 300_000) + b"end")
    destination.write_bytes(b"old")

    atomic_copy_file(source, destination)

    assert destination.read_bytes() == source.read_bytes()
    assert list(tmp_path.glob(".destination.bin.*.tmp")) == []


def test_atomic_copy_failure_preserves_existing_file(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")

    def fail_replace(source_path, destination_path):
        raise OSError("replace failed")

    monkeypatch.setattr("os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_copy_file(source, destination)

    assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob(".destination.bin.*.tmp")) == []


def test_sha256_file_reads_large_files_in_chunks(tmp_path):
    import hashlib

    path = tmp_path / "large.bin"
    content = (b"digest-data" * 200_000) + b"end"
    path.write_bytes(content)

    assert sha256_file(path) == hashlib.sha256(content).hexdigest()


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


def test_restore_rejects_manifest_content_mismatch(tmp_path, monkeypatch):
    import io

    from termux_agent import cli

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        '{"app":"termux-agent","config":"config.yaml","sessions":0}'
    )
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    assert cli.cmd_restore(str(bundle)) == 1
    assert "config file mismatch" in output.getvalue()


def test_restore_rejects_session_count_mismatch(tmp_path, monkeypatch):
    import io

    from termux_agent import cli

    bundle = tmp_path / "bundle"
    sessions = bundle / "sessions"
    sessions.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        '{"app":"termux-agent","sessions":0}'
    )
    (sessions / "unexpected.jsonl").write_text(
        '{"role":"user","content":"hello"}\n'
    )
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    assert cli.cmd_restore(str(bundle)) == 1
    assert "session count mismatch" in output.getvalue()


def test_restore_preflight_prevents_partial_write(tmp_path, monkeypatch):
    import io

    from termux_agent import cli, session

    bundle = tmp_path / "bundle"
    sessions = bundle / "sessions"
    sessions.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        '{"app":"termux-agent","version":"test","sessions":1}'
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


def test_streamed_bundle_round_trips_through_restore(tmp_path, monkeypatch):
    import io
    import json

    from termux_agent import agent, cli, session

    source = tmp_path / "source"
    source.mkdir()
    config = source / "config.yaml"
    config.write_text("provider: test\n")
    sessions = source / "sessions"
    sessions.mkdir()
    (sessions / "stream-id.jsonl").write_text(
        '{"role":"user","content":"hello"}\n'
    )
    monkeypatch.setattr(cli, "CONFIG_FILE", config)
    monkeypatch.setattr(session, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(session, "NOTES_FILE", source / "notes.json")
    monkeypatch.setattr(agent, "MEMORY_FILE", source / "memory.md")

    class WriteOnlyBuffer:
        def __init__(self):
            self.data = bytearray()

        def write(self, chunk):
            self.data.extend(chunk)
            return len(chunk)

        def flush(self):
            pass

    archive = WriteOnlyBuffer()

    class Output:
        buffer = archive

    monkeypatch.setattr(cli.sys, "stdout", Output())
    assert cli.cmd_bundle("-") == 0

    destination = tmp_path / "destination"
    monkeypatch.setattr(cli, "CONFIG_DIR", destination)
    monkeypatch.setattr(cli, "CONFIG_FILE", destination / "config.yaml")
    monkeypatch.setattr(session, "SESSIONS_DIR", destination / "sessions")
    archive_input = io.BytesIO(bytes(archive.data))

    class Input:
        buffer = archive_input

    monkeypatch.setattr(cli.sys, "stdin", Input())
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_restore("-") == 0
    assert (destination / "config.yaml").read_text() == "provider: test\n"
    restored = destination / "sessions" / "stream-id.jsonl"
    assert json.loads(restored.read_text())["content"] == "hello"


def test_reused_bundle_removes_only_stale_managed_files(tmp_path, monkeypatch):
    import io

    from termux_agent import agent, cli, session

    source = tmp_path / "source"
    source.mkdir()
    config = source / "config.yaml"
    config.write_text("provider: test\n")
    sessions = source / "sessions"
    sessions.mkdir()
    active = sessions / "active.jsonl"
    active.write_text('{"role":"user","content":"active"}\n')
    monkeypatch.setattr(cli, "CONFIG_FILE", config)
    monkeypatch.setattr(session, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(session, "NOTES_FILE", source / "notes.json")
    monkeypatch.setattr(agent, "MEMORY_FILE", source / "memory.md")
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())

    bundle = tmp_path / "bundle"
    stale_sessions = bundle / "sessions"
    stale_sessions.mkdir(parents=True)
    (stale_sessions / "stale.jsonl").write_text("stale")
    (bundle / "memory.md").write_text("stale memory")
    (bundle / "unrelated.txt").write_text("keep")

    assert cli.cmd_bundle(str(bundle)) == 0

    assert (bundle / "sessions" / "active.jsonl").is_file()
    assert not (bundle / "sessions" / "stale.jsonl").exists()
    assert not (bundle / "memory.md").exists()
    assert (bundle / "unrelated.txt").read_text() == "keep"


def test_no_sessions_removes_sessions_from_reused_bundle(tmp_path, monkeypatch):
    import io

    from termux_agent import agent, cli, session

    source = tmp_path / "source"
    source.mkdir()
    config = source / "config.yaml"
    config.write_text("provider: test\n")
    sessions = source / "sessions"
    sessions.mkdir()
    (sessions / "current.jsonl").write_text("current")
    monkeypatch.setattr(cli, "CONFIG_FILE", config)
    monkeypatch.setattr(session, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(session, "NOTES_FILE", source / "notes.json")
    monkeypatch.setattr(agent, "MEMORY_FILE", source / "memory.md")
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())

    bundle = tmp_path / "bundle"
    assert cli.cmd_bundle(str(bundle), include_sessions=True) == 0
    assert list((bundle / "sessions").glob("*.jsonl"))

    assert cli.cmd_bundle(str(bundle), include_sessions=False) == 0
    assert list((bundle / "sessions").glob("*.jsonl")) == []


def test_restore_rejects_tampered_bundle_file(tmp_path, monkeypatch):
    import io

    from termux_agent import agent, cli, session

    source = tmp_path / "source"
    source.mkdir()
    config = source / "config.yaml"
    config.write_text("provider: original\n")
    sessions = source / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(cli, "CONFIG_FILE", config)
    monkeypatch.setattr(session, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(session, "NOTES_FILE", source / "notes.json")
    monkeypatch.setattr(agent, "MEMORY_FILE", source / "memory.md")
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())

    bundle = tmp_path / "bundle"
    assert cli.cmd_bundle(str(bundle)) == 0
    (bundle / "config.yaml").write_text("provider: tampered\n")

    destination = tmp_path / "destination"
    monkeypatch.setattr(cli, "CONFIG_DIR", destination)
    monkeypatch.setattr(session, "SESSIONS_DIR", destination / "sessions")
    assert cli.cmd_restore(str(bundle)) == 1
    assert "checksum mismatch for config.yaml" in cli.sys.stdout.getvalue()
    assert not destination.exists()


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("config.yaml", "providers: [unterminated", "Invalid bundle config.yaml"),
        ("config.yaml", "- not\n- a\n- mapping\n", "expected a YAML mapping"),
        ("notes.json", "not-json", "Invalid bundle notes.json"),
        ("notes.json", '{"session": 42}', "expected string keys and values"),
    ],
)
def test_restore_rejects_invalid_metadata_content(
    tmp_path, monkeypatch, filename, content, message
):
    import io
    import json

    from termux_agent import cli

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = {
        "app": "termux-agent",
        "sessions": 0,
        "config": "config.yaml" if filename == "config.yaml" else None,
        "notes": "notes.json" if filename == "notes.json" else None,
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    (bundle / filename).write_text(content)
    destination = tmp_path / "destination"
    monkeypatch.setattr(cli, "CONFIG_DIR", destination)
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    assert cli.cmd_restore(str(bundle)) == 1
    assert message in output.getvalue()
    assert not destination.exists()


@pytest.mark.parametrize(
    "content",
    [
        "not-json\n",
        '{"role":"tool","content":"x"}\n',
        '{"role":"user","content":42}\n',
    ],
)
def test_restore_rejects_invalid_session_records(tmp_path, monkeypatch, content):
    import io

    from termux_agent import cli, session

    bundle = tmp_path / "bundle"
    sessions = bundle / "sessions"
    sessions.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        '{"app":"termux-agent","sessions":1}'
    )
    (sessions / "invalid.jsonl").write_text(content)
    destination = tmp_path / "destination"
    monkeypatch.setattr(cli, "CONFIG_DIR", destination)
    monkeypatch.setattr(session, "SESSIONS_DIR", destination / "sessions")
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    assert cli.cmd_restore(str(bundle)) == 1
    assert "Invalid session file invalid.jsonl line 1" in output.getvalue()
    assert not destination.exists()


def test_restore_rejects_oversized_directory_file(tmp_path, monkeypatch):
    import io

    from termux_agent import cli

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        '{"app":"termux-agent","memory":"memory.md","sessions":0}'
    )
    memory = bundle / "memory.md"
    memory.write_bytes(b"x" * 20)
    monkeypatch.setattr(cli, "MAX_BUNDLE_TEXT_FILE_BYTES", 10)
    destination = tmp_path / "destination"
    monkeypatch.setattr(cli, "CONFIG_DIR", destination)
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)

    assert cli.cmd_restore(str(bundle)) == 1
    assert "file exceeds the per-file limit of 10 bytes" in output.getvalue()
    assert not destination.exists()
