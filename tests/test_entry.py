"""Tests for the dependency-light console entry point."""
import json

from termux_agent import entry


def test_entry_version_fast_path(capsys):
    assert entry.main(["--version"]) == 0
    assert capsys.readouterr().out == "termux-agent 1.2.0\n"


def test_entry_version_json_fast_path(capsys):
    assert entry.main(["--json", "--version"]) == 0
    assert json.loads(capsys.readouterr().out) == {"name": "termux-agent", "version": "1.2.0"}


def test_entry_delegates_other_arguments(monkeypatch):
    from termux_agent import cli

    seen = []
    monkeypatch.setattr(cli, "main", lambda argv: seen.append(argv) or 7)

    assert entry.main(["--health"]) == 7
    assert seen == [["--health"]]
