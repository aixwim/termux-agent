"""Regression coverage for bugs found by the full repository audit."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx


def test_project_config_under_home_reaches_working_directory(tmp_path, monkeypatch):
    from termux_agent import config

    home = tmp_path / "home"
    project = home / "project"
    nested = project / "src"
    nested.mkdir(parents=True)
    user_dir = home / ".termux-agent"
    project_dir = project / ".termux-agent"
    user_dir.mkdir()
    project_dir.mkdir()
    (user_dir / "config.yaml").write_text("temperature: 0.1\n")
    (project_dir / "config.yaml").write_text("temperature: 0.2\nmodel: project-model\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(config, "CONFIG_FILE", user_dir / "config.yaml")
    monkeypatch.chdir(nested)

    loaded = config.load_config()

    assert loaded["temperature"] == 0.2
    assert loaded["model"] == "project-model"


def test_server_client_cannot_elevate_auto_accept():
    from termux_agent.server import _request_auto_accept

    locked = SimpleNamespace(auto_accept=False)
    enabled = SimpleNamespace(auto_accept=True)
    assert _request_auto_accept(locked, {"auto_accept": True}) is False
    assert _request_auto_accept(enabled, {"auto_accept": False}) is False
    assert _request_auto_accept(enabled, {}) is True


def test_unauthenticated_browser_origin_is_rejected():
    from termux_agent.server import _authorized

    browser = SimpleNamespace(token=None, headers={"Origin": "https://evil.example"})
    command_line = SimpleNamespace(token=None, headers={})
    assert _authorized(browser) is False
    assert _authorized(command_line) is True


def test_public_server_bind_requires_token(monkeypatch):
    from termux_agent import cli

    errors = []
    monkeypatch.setattr(cli, "render_error", errors.append)
    result = cli.cmd_serve({}, "0.0.0.0", 8787, None, None, False, None)
    assert result == 2
    assert "token" in errors[0].lower()


def test_find_session_rejects_ambiguous_prefix(tmp_path, monkeypatch):
    from termux_agent import cli, session

    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path)
    (tmp_path / "same-one.jsonl").write_text('{"role":"user","content":"one"}\n')
    (tmp_path / "same-two.jsonl").write_text('{"role":"user","content":"two"}\n')
    assert cli.find_session("same") is None
    assert cli.find_session("same-one")[0].name == "same-one.jsonl"


def test_one_shot_resume_appends_only_new_turn(tmp_path, monkeypatch, capsys):
    from termux_agent import cli, session

    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path)
    session.record_messages(
        [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ],
        "zen",
        "model",
        session_id="resume-me",
    )

    class FakeAgent:
        def __init__(self):
            self.provider = SimpleNamespace(name="zen", model="model")
            self.system_prompt = "system"
            self.messages = [{"role": "system", "content": "system"}]
            self.last_error = None

        def run(self, prompt, **kwargs):
            self.messages.extend(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "new answer"},
                ]
            )
            return "new answer"

    monkeypatch.setattr(cli, "build_agent", lambda *args, **kwargs: FakeAgent())
    cfg = {"provider": "zen", "providers": {"zen": {}}}
    assert cli.cmd_resume(cfg, "resume-me", "new question", quiet=True) == 0
    capsys.readouterr()
    messages = session.session_messages(tmp_path / "resume-me.jsonl")
    assert messages == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "new answer"},
    ]


def test_anthropic_usage_is_normalized():
    from termux_agent.providers.anthropic import AnthropicProvider

    events = [
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 12}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "ok"},
        },
        {"type": "message_delta", "usage": {"output_tokens": 3}},
    ]
    payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=payload)
        )
    ) as client:
        provider = AnthropicProvider("https://example.test", "model", client=client)
        streamed = list(provider.stream([{"role": "user", "content": "hi"}]))
    usage = [event.usage for event in streamed if event.kind == "usage"]
    assert usage == [
        {"prompt_tokens": 12, "total_tokens": 12},
        {"completion_tokens": 3, "total_tokens": 3},
    ]


def test_read_file_streams_instead_of_using_read_text(tmp_path, monkeypatch):
    from termux_agent.tools.base import ToolContext
    from termux_agent.tools.files import read_file

    path = tmp_path / "large.txt"
    path.write_text("\n".join(f"line {index}" for index in range(10_000)))
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("eager read")),
    )
    result = read_file(
        {"path": str(path), "start_line": 9999, "end_line": 10000},
        ToolContext(tmp_path, max_output_chars=200),
    )
    assert "9999: line 9998" in result
    assert "10000: line 9999" in result


def test_shell_timeout_kills_descendant_process(tmp_path):
    from termux_agent.tools.base import ToolContext
    from termux_agent.tools.shell import run_command

    marker = tmp_path / "orphaned"
    command = f"sh -c '(sleep 0.4; touch {marker}) & wait'"
    context = ToolContext(tmp_path, confirm_commands=False, command_timeout=0)
    result = run_command({"command": command}, context)
    assert "exceeded timeout" in result
    time.sleep(0.6)
    assert not marker.exists()
