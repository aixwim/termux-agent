"""Regression tests for bounded-memory session metadata reads."""
import json
from pathlib import Path

from termux_agent.session import iter_session, read_session, session_meta


def test_session_meta_counts_lines_and_finds_early_metadata(tmp_path: Path):
    path = tmp_path / "large.jsonl"
    records = [
        {"provider": "openai", "model": "small", "role": "system", "content": "rules"},
        {"role": "user", "content": "first request"},
    ]
    records.extend({"role": "assistant", "content": f"answer {i}"} for i in range(500))
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    count, first_user, info = session_meta(path)

    assert count == len(records)
    assert first_user == "first request"
    assert info == {"provider": "openai", "model": "small"}


def test_session_meta_tolerates_malformed_records(tmp_path: Path):
    path = tmp_path / "damaged.jsonl"
    path.write_text(
        '{not json}\n{"role":"user","content":"recoverable"}\n',
        encoding="utf-8",
    )

    count, first_user, info = session_meta(path)

    assert count == 2
    assert first_user == "recoverable"
    assert info == {}


def test_session_meta_finds_first_user_after_long_preamble(tmp_path: Path):
    path = tmp_path / "long-preamble.jsonl"
    records = [{"role": "system", "content": f"rule {i}"} for i in range(250)]
    records.append({"provider": "local", "model": "tiny", "role": "user", "content": "late request"})
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    count, first_user, info = session_meta(path)

    assert count == 251
    assert first_user == "late request"
    assert info == {"provider": "local", "model": "tiny"}


def test_iter_session_streams_valid_dict_records(tmp_path: Path):
    path = tmp_path / "mixed.jsonl"
    path.write_text('{"role":"user","content":"one"}\ninvalid\n[1, 2]\n{"role":"assistant","content":"two"}\n')

    streamed = list(iter_session(path))

    assert streamed == read_session(path)
    assert [record["content"] for record in streamed] == ["one", "two"]
