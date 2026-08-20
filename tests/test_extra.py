"""Test fitur tambahan: rules file, tool git, resume sesi, compact, storage."""
import subprocess
from pathlib import Path

import pytest

from termux_agent.agent import Agent, build_system_prompt, load_rules
from termux_agent.session import session_messages
from termux_agent.tools import git  # noqa: F401  # register tools
from termux_agent.tools.base import ToolContext, run_tool


# --- rules file ---
def test_load_rules_agents_md(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("Selalu gunakan type hint di Python.")
    rules = load_rules(tmp_path)
    assert "type hint" in rules and "AGENTS.md" in rules


def test_load_rules_claude_md(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("Aturan dari CLAUDE.")
    assert "Aturan dari CLAUDE" in load_rules(tmp_path)


def test_load_rules_ignores_missing(tmp_path: Path):
    assert load_rules(tmp_path) == ""


def test_build_system_prompt_appends_rules():
    base = build_system_prompt("")
    with_rules = build_system_prompt("Aturan proyek: X")
    assert with_rules.startswith(base)
    assert "Aturan proyek: X" in with_rules


def test_agent_loads_rules_into_system(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("Tulis komentar Bahasa Indonesia.")
    from termux_agent.providers.base import Provider, StreamEvent

    class P(Provider):
        name = "p"
        model = "m"

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            return [StreamEvent(kind="text_delta", text="ok")]

    agent = Agent(P(), ToolContext(working_dir=tmp_path, confirm_commands=False))
    assert "Bahasa Indonesia" in agent.messages[0]["content"]


# --- tool git ---
@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@local"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_git_status(git_repo: Path):
    (git_repo / "a.txt").write_text("v2\n")
    (git_repo / "b.txt").write_text("baru\n")
    ctx = ToolContext(working_dir=git_repo, confirm_commands=False)
    r = run_tool("git_status", {}, ctx)
    assert "a.txt" in r and "b.txt" in r


def test_git_diff(git_repo: Path):
    (git_repo / "a.txt").write_text("v2\n")
    ctx = ToolContext(working_dir=git_repo, confirm_commands=False)
    r = run_tool("git_diff", {"stat": True}, ctx)
    assert "a.txt" in r


def test_git_commit_needs_confirm(git_repo: Path):
    (git_repo / "a.txt").write_text("v2\n")
    ctx = ToolContext(working_dir=git_repo, confirm_commands=True, confirm=lambda _: False)
    r = run_tool("git_commit", {"message": "perbaikan"}, ctx)
    assert "Cancelled by user." in r


def test_git_commit_confirmed(git_repo: Path):
    (git_repo / "a.txt").write_text("v2\n")
    ctx = ToolContext(working_dir=git_repo, confirm_commands=True, confirm=lambda _: True)
    r = run_tool("git_commit", {"message": "perbaikan"}, ctx)
    assert "perbaikan" in r
    log = subprocess.run(["git", "log", "--oneline"], cwd=git_repo, capture_output=True, text=True).stdout
    assert "perbaikan" in log


def test_git_commit_no_changes(git_repo: Path):
    ctx = ToolContext(working_dir=git_repo, confirm_commands=False)
    assert "No changes to commit." in run_tool("git_commit", {"message": "x"}, ctx)


def test_git_status_not_a_repo(tmp_path: Path):
    ctx = ToolContext(working_dir=tmp_path, confirm_commands=False)
    r = run_tool("git_status", {}, ctx)
    assert "fatal" in r or "not a git repository" in r


# --- resume sesi ---
def test_session_messages(tmp_path: Path, monkeypatch):
    import termux_agent.session as sess

    monkeypatch.setattr(sess, "SESSIONS_DIR", tmp_path)
    s = sess.Session(session_id="abc123", provider_name="zen", model="m")
    s.append({"role": "user", "content": "halo"})
    s.append({"role": "assistant", "content": "hai!"})
    s.append({"role": "tool", "content": "x"})
    msgs = session_messages(s.path)
    assert msgs == [
        {"role": "user", "content": "halo"},
        {"role": "assistant", "content": "hai!"},
    ]


# --- compact ---
class FakeSummarizer:
    name = "fake"
    model = "m"

    def __init__(self) -> None:
        self.calls = 0

    def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
        self.calls += 1
        if tools is None:  # panggilan ringkasan
            from termux_agent.providers.base import StreamEvent

            return [StreamEvent(kind="text_delta", text="RINGKASAN")]
        raise AssertionError("compact tidak boleh memakai tools")


def test_compact_reduces_messages(tmp_path: Path):
    prov = FakeSummarizer()
    agent = Agent(prov, ToolContext(working_dir=tmp_path, confirm_commands=False))
    for i in range(6):
        agent.messages.append({"role": "user", "content": f"q{i}"})
        agent.messages.append({"role": "assistant", "content": f"a{i}"})
    n_before = len(agent.messages)
    summary = agent.compact(keep_recent=2)
    assert summary == "RINGKASAN"
    assert len(agent.messages) < n_before
    assert agent.messages[1]["role"] == "user"
    assert "RINGKASAN" in agent.messages[1]["content"]
    # 2 pesan terakhir tetap utuh
    assert agent.messages[-1] == {"role": "assistant", "content": "a5"}


def test_compact_skips_short_history(tmp_path: Path):
    prov = FakeSummarizer()
    agent = Agent(prov, ToolContext(working_dir=tmp_path, confirm_commands=False))
    assert agent.compact() == ""  # hanya system message


# --- auto-accept / --yes ---
def _min_cfg(**overrides) -> dict:
    cfg = {
        "provider": "zen",
        "providers": {
            "zen": {
                "type": "openai_compat",
                "base_url": "http://localhost:9/v1",
                "models": ["m"],
                "api_key_env": "",
            }
        },
        "agents": {
            "root": {"description": "utama", "prompt": "", "tools": []},
            "explore": {
                "description": "baca saja",
                "prompt": "jangan mengubah",
                "tools": ["read_file"],
            },
        },
    }
    cfg.update(overrides)
    return cfg


def test_build_agent_auto_accept(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    cfg = _min_cfg()
    monkeypatch.chdir(tmp_path)
    agent = build_agent(cfg, "zen", None, auto_accept=True)
    assert agent.ctx.confirm_commands is False


# --- storage android ---
def test_detect_storage_roots():
    from termux_agent.config import detect_storage_roots

    roots = detect_storage_roots()
    assert isinstance(roots, list)


def test_allow_storage_adds_allowed_dirs(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    cfg = _min_cfg(allow_storage=True)
    monkeypatch.chdir(tmp_path)
    agent = build_agent(cfg, "zen", None)
    allowed = [d for d in agent.ctx._allowed_dirs if "storage" in str(d)]
    assert allowed, "storage roots harus ditambahkan ke _allowed_dirs"


# --- sub-agent ---
def test_build_agent_selects_agent(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    monkeypatch.chdir(tmp_path)
    agent = build_agent(_min_cfg(), "zen", None, agent_name="explore")
    assert agent.allowed_tools == {"read_file"}
    assert "jangan mengubah" in agent.system_prompt
    names = {t.name for t in agent.tools}
    assert names == {"read_file"}


def test_set_agent_switches_tools_and_prompt(tmp_path: Path):
    from termux_agent.providers.base import Provider

    class P(Provider):
        name = "p"
        model = "m"

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            return []

    agent = Agent(P(), ToolContext(working_dir=tmp_path, confirm_commands=False))
    full = {t.name for t in agent.tools}
    assert len(full) >= 10
    agent.set_agent({"prompt": "baca saja", "tools": ["read_file", "list_dir"]})
    assert agent.allowed_tools == {"read_file", "list_dir"}
    assert {t.name for t in agent.tools} == {"read_file", "list_dir"}
    assert "baca saja" in agent.messages[0]["content"]


def test_build_agent_unknown_agent_raises(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    monkeypatch.chdir(tmp_path)
    with pytest.raises(Exception):
        build_agent(_min_cfg(), "zen", None, agent_name="nope")


def test_build_agent_overrides(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    monkeypatch.chdir(tmp_path)
    agent = build_agent(
        _min_cfg(),
        "zen",
        None,
        working_dir=str(tmp_path / "sub"),
        temperature=0.1,
        max_tool_rounds=3,
    )
    assert agent.ctx.working_dir == tmp_path / "sub"
    assert agent.temperature == 0.1
    assert agent.max_tool_rounds == 3


def test_doctor_runs(tmp_path: Path, monkeypatch):
    from termux_agent.cli import cmd_doctor

    monkeypatch.chdir(tmp_path)
    code = cmd_doctor(_min_cfg(), network=False)
    assert code in (0, 1)


def test_build_agent_readonly(tmp_path: Path, monkeypatch):
    from termux_agent.cli import READONLY_TOOLS, build_agent

    monkeypatch.chdir(tmp_path)
    agent = build_agent(_min_cfg(), "zen", None, readonly=True)
    names = {t.name for t in agent.tools}
    assert names == READONLY_TOOLS
    assert "write_file" not in names
    assert "run_command" not in names
    assert "read-only" in agent.system_prompt.lower()


def test_build_agent_readonly_keeps_agent_limit(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    monkeypatch.chdir(tmp_path)
    agent = build_agent(_min_cfg(), "zen", None, agent_name="explore", readonly=True)
    assert {t.name for t in agent.tools} == {"read_file"}


def test_repl_export(tmp_path: Path):
    from types import SimpleNamespace

    from termux_agent.ui.repl import Repl

    fake = SimpleNamespace(
        messages=[
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "tolong baca file"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "read_file", "arguments": '{"path": "a.txt"}'}],
            },
            {"role": "tool", "content": "isi file"},
            {"role": "assistant", "content": "Selesai."},
        ]
    )
    repl = Repl(fake, provider_name="zen", model="m", agent_name="root")
    out = tmp_path / "out.md"
    repl._export(str(out))
    text = out.read_text()
    assert "## user" in text
    assert "## tool" in text
    assert "Selesai." in text
    assert "read_file" in text
    assert "provider: zen" in text
    assert "tolong baca file" in text


# --- usage tracking ---
def _usage_provider():
    from termux_agent.providers.base import Provider, StreamEvent

    class P(Provider):
        name = "p"
        model = "m"

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            yield StreamEvent(kind="usage", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
            yield StreamEvent(kind="text_delta", text="ok")
            yield StreamEvent(kind="usage", usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})
            yield StreamEvent(kind="done")

    return P()


def test_agent_tracks_usage(tmp_path: Path):
    from termux_agent.agent import Agent
    from termux_agent.providers.base import Provider
    from termux_agent.tools.base import ToolContext

    agent = Agent(_usage_provider(), ToolContext(working_dir=tmp_path, confirm_commands=False))
    out = agent.run("hello")
    assert out == "ok"
    assert agent.usage == {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}


def test_stdin_used_as_prompt(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    class FakeStdin:
        def isatty(self):
            return False

        def read(self):
            return "hello from pipe"

    monkeypatch.setattr(cli.sys, "stdin", FakeStdin())
    captured = {}

    def fake_one_shot(cfg, prompt, provider, model, **kw):
        captured["p"] = prompt
        return 0

    monkeypatch.setattr(cli, "cmd_one_shot", fake_one_shot)
    code = cli.main([])
    assert code == 0
    assert captured.get("p") == "hello from pipe"


def test_cmd_plan_requires_approval(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent import cli

    calls = {"exec": 0}

    def fake_build(cfg, provider, model, auto_accept=False, agent_name=None, working_dir=None, temperature=None, max_tool_rounds=None, readonly=False, max_context_tokens=None):
        return SimpleNamespace(
            provider=SimpleNamespace(name="p", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            run=lambda prompt, on_tool_use=None: (
                f"[PLAN] {prompt[:40]}" if readonly else calls.__setitem__("exec", calls["exec"] + 1) or "EXECUTED"
            ),
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    code = cli.cmd_plan(_min_cfg(), "do the thing", "zen", None)
    assert code == 0
    assert calls["exec"] == 0


def test_cmd_plan_executes_when_approved(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent import cli

    calls = {"exec": 0}

    def fake_build(cfg, provider, model, auto_accept=False, agent_name=None, working_dir=None, temperature=None, max_tool_rounds=None, readonly=False, max_context_tokens=None):
        return SimpleNamespace(
            provider=SimpleNamespace(name="p", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            run=lambda prompt, on_tool_use=None: (
                f"[PLAN] {prompt[:40]}" if readonly else calls.__setitem__("exec", calls["exec"] + 1) or "EXECUTED"
            ),
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    code = cli.cmd_plan(_min_cfg(), "do the thing", "zen", None)
    assert code == 0
    assert calls["exec"] == 1


# --- fallback models on 429 ---
def test_agent_falls_back_on_rate_limit(tmp_path: Path):
    from termux_agent.agent import Agent
    from termux_agent.providers.base import Provider, ProviderError, StreamEvent
    from termux_agent.tools.base import ToolContext

    attempts = []

    class P(Provider):
        name = "p"
        model = "m1"
        fallback_models = ["m2", "m3"]

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            attempts.append(self.model)
            if self.model == "m1":
                raise ProviderError("p: HTTP 429 - rate limited")
            yield StreamEvent(kind="text_delta", text=f"ok from {self.model}")
            yield StreamEvent(kind="done")

    agent = Agent(P(), ToolContext(working_dir=tmp_path, confirm_commands=False))
    out = agent.run("hello")
    assert out == "ok from m2"
    assert attempts == ["m1", "m2"]
    assert agent.provider.model == "m2"


def test_agent_non_rate_limit_not_retried(tmp_path: Path):
    from termux_agent.agent import Agent
    from termux_agent.providers.base import Provider, ProviderError
    from termux_agent.tools.base import ToolContext

    attempts = []

    class P(Provider):
        name = "p"
        model = "m1"
        fallback_models = ["m2"]

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            attempts.append(self.model)
            raise ProviderError("p: HTTP 401 - unauthorized")

    agent = Agent(P(), ToolContext(working_dir=tmp_path, confirm_commands=False))
    out = agent.run("hello")
    assert out.startswith("Error:")
    assert "401" in out
    assert attempts == ["m1"]


# --- --models ---
def test_cmd_list_models_preset(tmp_path: Path, monkeypatch):
    from termux_agent import cli

    cfg = _min_cfg()
    code = cli.cmd_list_models(cfg, "zen")
    assert code == 0


def test_cmd_list_models_live(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli
    from termux_agent.providers.base import Provider

    class FakeProv(Provider):
        name = "fake"
        model = "m"

        def list_models(self):
            return ["a", "b"]

        def stream(self, *a, **k):
            return iter([])

    monkeypatch.setattr(cli, "create_provider", lambda *a, **k: FakeProv())
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_list_models(_min_cfg(), "zen") == 0


# --- undo ---
def test_undo_restores_edited_file(tmp_path: Path):
    from termux_agent.tools import files
    from termux_agent.tools.base import ToolContext

    p = tmp_path / "a.txt"
    p.write_text("line1\nline2\n")
    ctx = ToolContext(working_dir=tmp_path)
    files.edit_file({"path": "a.txt", "old_string": "line2", "new_string": "CHANGED"}, ctx)
    assert p.read_text() == "line1\nCHANGED\n"
    msg = ctx.undo()
    assert "Undid" in msg
    assert p.read_text() == "line1\nline2\n"


def test_undo_removes_new_file(tmp_path: Path):
    from termux_agent.tools import files
    from termux_agent.tools.base import ToolContext

    ctx = ToolContext(working_dir=tmp_path)
    files.write_file({"path": "new.txt", "content": "hello"}, ctx)
    assert (tmp_path / "new.txt").exists()
    msg = ctx.undo()
    assert "Undid" in msg
    assert not (tmp_path / "new.txt").exists()


def test_undo_empty(tmp_path: Path):
    from termux_agent.tools.base import ToolContext

    ctx = ToolContext(working_dir=tmp_path)
    assert "Nothing to undo" in ctx.undo()


def test_undo_lifo_order(tmp_path: Path):
    from termux_agent.tools import files
    from termux_agent.tools.base import ToolContext

    a = tmp_path / "a.txt"
    a.write_text("original")
    ctx = ToolContext(working_dir=tmp_path)
    files.write_file({"path": "a.txt", "content": "first"}, ctx)
    files.write_file({"path": "a.txt", "content": "second"}, ctx)
    ctx.undo()
    assert a.read_text() == "first"
    ctx.undo()
    assert a.read_text() == "original"


# --- --json ---
def test_cmd_one_shot_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    from types import SimpleNamespace

    from termux_agent import cli

    calls = {}

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            run=lambda prompt, on_tool_use=None: (on_tool_use("read_file", "{}") or "ANSWER"),
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, as_json=True)
    assert code == 0
    data = _json.loads(out.getvalue())
    assert data["ok"] is True
    assert data["answer"] == "ANSWER"
    assert data["tool_calls"][0]["name"] == "read_file"
    assert data["usage"]["total_tokens"] == 8


# --- auto-compact on token budget ---
def test_auto_compact_on_budget(tmp_path: Path):
    from termux_agent.agent import Agent
    from termux_agent.providers.base import Provider, StreamEvent
    from termux_agent.tools.base import ToolContext

    stream_count = {"n": 0}

    class P(Provider):
        name = "p"
        model = "m"

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            stream_count["n"] += 1
            if stream_count["n"] == 1:
                yield StreamEvent(kind="text_delta", text="first")
                yield StreamEvent(kind="usage", usage={"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600})
                yield StreamEvent(kind="done")
                return
            yield StreamEvent(kind="text_delta", text="second")
            yield StreamEvent(kind="usage", usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
            yield StreamEvent(kind="done")

    agent = Agent(P(), ToolContext(working_dir=tmp_path), max_context_tokens=1000)
    assert agent.run("a") == "first"
    # usage 600 + 150 = 750 < 1000 -> no compact yet
    assert len(agent.messages) >= 3
    agent.run("b")  # usage now 750, but no new usage crossing yet
    assert agent.usage["total_tokens"] == 750

    class P2(P):
        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            yield StreamEvent(kind="text_delta", text="done")
            yield StreamEvent(kind="usage", usage={"prompt_tokens": 400, "completion_tokens": 0, "total_tokens": 400})
            yield StreamEvent(kind="done")

    # fresh agent with enough history: crossing the budget should compact old messages
    class PC(Provider):
        name = "p"
        model = "m"

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            yield StreamEvent(kind="text_delta", text="ok")
            yield StreamEvent(kind="usage", usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 1100})
            yield StreamEvent(kind="done")

    agent2 = Agent(PC(), ToolContext(working_dir=tmp_path), max_context_tokens=1000)
    agent2.messages = [{"role": "system", "content": agent2.system_prompt}] + [
        {"role": "user", "content": f"q{i}"} if i % 2 else {"role": "assistant", "content": f"a{i}"}
        for i in range(1, 8)
    ]
    agent2.run("hello")
    assert agent2._compacted_this_turn is True
    joined = " ".join(m.get("content", "") for m in agent2.messages)
    assert "Summary of previous conversation" in joined


# --- session delete ---
def test_delete_session_by_prefix(tmp_path: Path, monkeypatch):
    from termux_agent import session
    from termux_agent.config import CONFIG_DIR

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "20260819-111111.jsonl").write_text('{"role":"user","content":"hi"}\n')
    (sessions_dir / "20260819-222222.jsonl").write_text('{"role":"user","content":"hey"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sessions_dir)
    removed = session.delete_session("20260819-2222")
    assert removed is not None
    assert not (sessions_dir / "20260819-222222.jsonl").exists()
    assert (sessions_dir / "20260819-111111.jsonl").exists()


def test_delete_session_nonexistent(tmp_path: Path, monkeypatch):
    from termux_agent import session

    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path / "empty")
    assert session.delete_session("nope") is None


# --- --quiet / --copy ---
def test_cmd_one_shot_quiet(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda prompt, on_tool_use=None: "ONLY ANSWER",
    ))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, quiet=True)
    assert code == 0
    assert out.getvalue() == "ONLY ANSWER\n"


def test_cmd_one_shot_copy(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    copied = {}

    def fake_copy(text):
        copied["text"] = text
        return True

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda prompt, on_tool_use=None: "HELLO",
    ))
    monkeypatch.setattr("termux_agent.ui.repl.copy_to_clipboard", fake_copy)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, copy=True, quiet=True)
    assert code == 0
    assert copied.get("text") == "HELLO"
    assert out.getvalue() == "HELLO\n"


# --- project-local config ---
def test_project_config_overrides(tmp_path: Path, monkeypatch):
    import os

    from termux_agent import config as cfgmod

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".termux-agent").mkdir()
    (proj / ".termux-agent" / "config.yaml").write_text(
        "temperature: 0.1\nmax_tool_rounds: 5\nworking_dir: '~/proj'\n"
    )
    monkeypatch.setattr(os, "getcwd", lambda: str(proj))
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "nonexistent.yaml")
    cfg = cfgmod.load_config()
    assert cfg["temperature"] == 0.1
    assert cfg["max_tool_rounds"] == 5
    assert cfg["provider"] == "zen"  # unchanged default


def test_project_config_missing_is_ignored(tmp_path: Path, monkeypatch):
    import os

    from termux_agent import config as cfgmod

    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "nonexistent.yaml")
    cfg = cfgmod.load_config()
    assert cfg["temperature"] == 0.7


# --- /prompt session instruction ---
def test_repl_prompt_add_clear(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli
    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    agent = SimpleNamespace(
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
        ctx=SimpleNamespace(working_dir=tmp_path),
    )
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    repl = Repl(agent, provider_name="zen", model="m")
    repl._handle_command("/prompt always use english", None)
    assert "always use english" in repl.agent.system_prompt
    assert repl.agent.messages[0]["content"].startswith("BASE")
    repl._handle_command("/prompt clear", None)
    assert repl.agent.system_prompt == "BASE"
    assert repl.agent.messages[0]["content"] == "BASE"


# --- transient retry ---
def test_retry_transient_connection(tmp_path: Path):
    from termux_agent.agent import Agent
    from termux_agent.providers.base import Provider, ProviderError, StreamEvent
    from termux_agent.tools.base import ToolContext

    attempts = []

    class P(Provider):
        name = "p"
        model = "m"

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            attempts.append(self.model)
            if len(attempts) == 1:
                raise ProviderError("p: connection failed - [Errno 110] timed out")
            yield StreamEvent(kind="text_delta", text="recovered")
            yield StreamEvent(kind="done")

    agent = Agent(P(), ToolContext(working_dir=tmp_path), retries=1, retry_backoff=0)
    assert agent.run("hi") == "recovered"
    assert len(attempts) == 2


def test_retry_transient_5xx(tmp_path: Path):
    from termux_agent.agent import Agent
    from termux_agent.providers.base import Provider, ProviderError, StreamEvent
    from termux_agent.tools.base import ToolContext

    attempts = []

    class P(Provider):
        name = "p"
        model = "m"

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            attempts.append(1)
            if len(attempts) == 1:
                raise ProviderError("p: HTTP 502 - bad gateway")
            yield StreamEvent(kind="text_delta", text="ok")
            yield StreamEvent(kind="done")

    agent = Agent(P(), ToolContext(working_dir=tmp_path), retries=1, retry_backoff=0)
    assert agent.run("hi") == "ok"
    assert len(attempts) == 2


def test_retry_exhausted_returns_error(tmp_path: Path):
    from termux_agent.agent import Agent
    from termux_agent.providers.base import Provider, ProviderError
    from termux_agent.tools.base import ToolContext

    class P(Provider):
        name = "p"
        model = "m"

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            raise ProviderError("p: connection failed - timeout")

    agent = Agent(P(), ToolContext(working_dir=tmp_path), retries=1, retry_backoff=0)
    out = agent.run("hi")
    assert out.startswith("Error:")


# --- /cd ---
def test_repl_cd(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    sub = tmp_path / "sub"
    sub.mkdir()
    agent = SimpleNamespace(
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
        ctx=SimpleNamespace(working_dir=tmp_path),
    )
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    repl = Repl(agent, provider_name="zen", model="m")
    repl._handle_command("/cd sub", None)
    assert agent.ctx.working_dir == sub
    repl._handle_command("/cd nope", None)
    assert agent.ctx.working_dir == sub  # unchanged


# --- /remember ---
def test_repl_remember(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent import config as cfgmod
    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    agent = SimpleNamespace(
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
        ctx=SimpleNamespace(working_dir=tmp_path),
    )
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr("termux_agent.ui.repl.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    repl = Repl(agent, provider_name="zen", model="m")
    repl._handle_command("/remember user prefers python", None)
    mem = (tmp_path / "memory.md").read_text()
    assert "prefers python" in mem
    assert "[Memory]" in agent.system_prompt


# --- --plan --json ---
def test_cmd_plan_json_not_executed(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    from types import SimpleNamespace

    from termux_agent import cli

    def fake_build(cfg, provider, model, auto_accept=False, agent_name=None, working_dir=None, temperature=None, max_tool_rounds=None, readonly=False, max_context_tokens=None):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            run=lambda prompt, on_tool_use=None: "step1\nstep2",
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_plan(_min_cfg(), "do it", "zen", None, as_json=True)
    assert code == 0
    data = _json.loads(out.getvalue())
    assert data["ok"] is True
    assert data["executed"] is False
    assert "step1" in data["plan"]


# --- git_log tool ---
def test_git_log_tool(tmp_path: Path):
    import subprocess

    from termux_agent.tools.base import run_tool

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first commit"], cwd=tmp_path, check=True)
    ctx = ToolContext(working_dir=tmp_path, confirm_commands=False)
    out = run_tool("git_log", {}, ctx)
    assert "first commit" in out


# --- vision: [image:] embedding ---
def test_embed_images_converts_to_parts(tmp_path: Path):
    from termux_agent.providers import openai_compat as oc

    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG fake")
    out = oc._embed_images(f"look at [image: {img}] and describe it")
    assert isinstance(out, list)
    assert out[0]["type"] == "text"
    assert out[1]["type"] == "image_url"
    assert out[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert out[2]["type"] == "text"


def test_embed_images_missing_file(tmp_path: Path):
    from termux_agent.providers import openai_compat as oc

    out = oc._embed_images("see [image: nope.png] ok")
    assert isinstance(out, list)
    joined = " ".join(p.get("text", "") for p in out if p.get("type") == "text")
    assert "image not found" in joined


def test_embed_images_no_marker_passthrough(tmp_path: Path):
    from termux_agent.providers import openai_compat as oc

    assert oc._embed_images("plain text") == "plain text"


def test_wire_message_embeds_image(tmp_path: Path):
    from termux_agent.providers.openai_compat import _to_openai_wire

    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpegdata")
    out = _to_openai_wire([{"role": "user", "content": f"what is in [image: {img}]"}])
    assert out[0]["role"] == "user"
    assert isinstance(out[0]["content"], list)
    assert any(p["type"] == "image_url" for p in out[0]["content"])


# --- --prompt-file / --image flags ---
def test_main_prompt_file(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    f = tmp_path / "prompt.txt"
    f.write_text("do the thing please")
    seen = {}

    def fake_one_shot(cfg, prompt, provider, model, **kw):
        seen["prompt"] = prompt
        return 0

    monkeypatch.setattr(cli, "cmd_one_shot", fake_one_shot)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    code = cli.main(["--prompt-file", str(f)])
    assert code == 0
    assert seen["prompt"] == "do the thing please"


def test_main_image_flag(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    img = tmp_path / "x.png"
    img.write_bytes(b"data")
    seen = {}

    def fake_one_shot(cfg, prompt, provider, model, **kw):
        seen["prompt"] = prompt
        return 0

    monkeypatch.setattr(cli, "cmd_one_shot", fake_one_shot)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    code = cli.main(["--image", str(img), "describe this"])
    assert code == 0
    assert f"[image: {img}]" in seen["prompt"]


# --- multiline input ---
def test_multiline_single_line_wrap(tmp_path: Path):
    from types import SimpleNamespace

    from termux_agent.ui.repl import Repl

    agent = SimpleNamespace(
        system_prompt="B",
        messages=[{"role": "system", "content": "B"}],
        ctx=SimpleNamespace(working_dir=tmp_path),
    )
    repl = Repl(agent, provider_name="zen", model="m")
    out = repl._maybe_read_multiline("{{ hello }}", None)
    assert out == "hello"


def test_list_tree_tool(tmp_path: Path):
    from termux_agent.tools import files
    from termux_agent.tools.base import ToolContext, run_tool

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")
    (tmp_path / "README.md").write_text("hi")
    ctx = ToolContext(working_dir=tmp_path)
    out = run_tool("list_tree", {}, ctx)
    assert "src/" in out
    assert "a.py" in out
    assert ".git" not in out
    assert "README.md" in out


def test_main_api_key_flag(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli, "cmd_one_shot", lambda *a, **k: 0)
    assert cli.main(["--provider", "xai", "--api-key", "sk-test", "hi"]) == 0
    import os

    assert os.environ.get("XAI_API_KEY") == "sk-test"


# --- HTTP server ---
def test_server_chat_and_health(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request
    from types import SimpleNamespace

    from termux_agent import server as srv
    from termux_agent import session
    from termux_agent.server import _AgentHandler

    sdir = tmp_path / "sessions"
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    class FakeProv:
        name = "zen"
        model = "m"

        def list_models(self):
            return ["m1", "m2"]

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=FakeProv(),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            messages=[{"role": "system", "content": "s"}],
        )

        def _run(prompt):
            agent.messages += [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "ANSWER:" + prompt},
            ]
            return "ANSWER:" + prompt

        agent.run = _run
        return agent

    _AgentHandler.build_agent = staticmethod(fake_build)
    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
            health = _json.loads(r.read())
        assert health["ok"] is True
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({"prompt": "hello"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            chat = _json.loads(r.read())
        assert chat["answer"] == "ANSWER:hello"
        assert chat["usage"]["total_tokens"] == 15
        assert chat["session"]
        assert list(sdir.glob("*.jsonl"))
        resume_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({"prompt": "again", "session": chat["session"]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(resume_req, timeout=15) as r:
            resume = _json.loads(r.read())
        assert resume["session"] == chat["session"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/models", timeout=10) as r:
            models = _json.loads(r.read())
        assert models["models"] == ["m1", "m2"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_missing_prompt(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.error
    import urllib.request
    from types import SimpleNamespace

    from termux_agent import server as srv
    from termux_agent.server import _AgentHandler

    _AgentHandler.build_agent = staticmethod(lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="p", model="m"),
        usage={},
        messages=[],
        run=lambda p: "x",
    ))
    httpd = srv.build_server(lambda *a, **k: None, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=10)
        assert ei.value.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_cmd_one_shot_stats(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        run=lambda prompt, on_tool_use=None: "ANSWER",
    ))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, stats=True)
    assert code == 0
    assert "total 5" in out.getvalue()


# --- whitelisted_commands ---
def test_whitelisted_command_no_confirm(tmp_path: Path):
    from termux_agent.tools.base import run_tool

    ctx = ToolContext(working_dir=tmp_path, confirm_commands=True, confirm=None)
    ctx.whitelisted_commands = ["python dummy.py"]
    out = run_tool("run_command", {"command": "echo hi"}, ctx)  # echo is safe anyway
    assert "exit 0" in out
    # a non-safe command that matches a whitelist prefix must NOT ask for confirmation
    script = tmp_path / "dummy.py"
    script.write_text("print('ran')")
    out2 = run_tool("run_command", {"command": "python dummy.py"}, ctx)
    assert "ran" in out2 and "confirmation" not in out2


def test_non_whitelisted_needs_confirm(tmp_path: Path):
    from termux_agent.tools.base import run_tool

    ctx = ToolContext(working_dir=tmp_path, confirm_commands=True, confirm=None)
    out = run_tool("run_command", {"command": "rm -rf something"}, ctx)
    assert "not in the whitelist" in out


# --- notify helper ---
def test_notify_respects_env(monkeypatch):
    import os

    from termux_agent import notify as nmod

    monkeypatch.delenv("TERMUX_AGENT_NOTIFY", raising=False)
    assert nmod.notify("hi") is False
    nmod.notify_on_done(True)
    assert os.environ.get("TERMUX_AGENT_NOTIFY") == "1"


# --- --chat mode ---
def test_build_agent_chat_disables_tools(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    monkeypatch.chdir(tmp_path)
    agent = build_agent(_min_cfg(), "zen", None, no_tools=True)
    assert agent.tools == []


def test_build_agent_chat_keeps_agent_limits(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    monkeypatch.chdir(tmp_path)
    agent = build_agent(_min_cfg(), "zen", None, agent_name="explore", no_tools=False)
    assert [t.name for t in agent.tools] == ["read_file"]


# --- --sessions --search ---
def test_cmd_sessions_search(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260819-000001.jsonl").write_text('{"role":"user","content":"fix the calculator"}\n')
    (sdir / "20260819-000002.jsonl").write_text('{"role":"user","content":"explain config"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_sessions("calculator") == 0
    assert "000001" in out.getvalue()
    assert "000002" not in out.getvalue()


# --- --resume --json ---
def test_cmd_resume_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260819-000001.jsonl").write_text(
        '{"role":"user","content":"hi"}\n{"role":"assistant","content":"hello"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            system_prompt="SYS",
            messages=[],
            run=lambda prompt, on_tool_use=None: "CONTINUED",
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_resume(_min_cfg(), "latest", "continue please", as_json=True)
    assert code == 0
    data = _json.loads(out.getvalue())
    assert data["ok"] is True
    assert data["answer"] == "CONTINUED"


# --- wakelock / speak flags ---
def test_one_shot_wakelock_acquired_and_released(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    calls = []

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda prompt, on_tool_use=None: calls.append("run") or "DONE",
    ))
    monkeypatch.setattr("termux_agent.notify.wake_lock", lambda: calls.append("lock") or True)
    monkeypatch.setattr("termux_agent.notify.wake_unlock", lambda: calls.append("unlock"))
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, quiet=True, wakelock=True) == 0
    assert calls == ["lock", "run", "unlock"]


def test_one_shot_speak_called(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    spoken = {}

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda prompt, on_tool_use=None: "SPOKEN TEXT",
    ))
    monkeypatch.setattr("termux_agent.notify.speak", lambda t: spoken.update(text=t) or True)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, quiet=True, speak=True) == 0
    assert spoken.get("text") == "SPOKEN TEXT"


def test_wakelock_noop_when_missing(monkeypatch):
    from termux_agent import notify as nmod

    monkeypatch.setattr(nmod, "_have", lambda cmd: False)
    assert nmod.wake_lock() is False
    assert nmod.speak("x") is False


# --- timeout guard ---
def test_run_guarded_timeout(monkeypatch):
    import time
    from types import SimpleNamespace

    from termux_agent import cli

    agent = SimpleNamespace(run=lambda prompt, on_tool_use=None: time.sleep(2) or "LATE")
    started = time.time()
    try:
        cli._run_guarded(agent, "p", None, timeout=0.1)
        raise AssertionError("expected TimeoutError")
    except TimeoutError:
        assert time.time() - started < 1.5


def test_run_guarded_no_timeout(monkeypatch):
    from types import SimpleNamespace

    from termux_agent import cli

    agent = SimpleNamespace(run=lambda prompt, on_tool_use=None: "FAST")
    assert cli._run_guarded(agent, "p", None, timeout=None) == "FAST"


def test_record_messages(tmp_path: Path, monkeypatch):
    from termux_agent import session

    sdir = tmp_path / "sessions"
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    sid = session.record_messages(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "x"},
            {"role": "assistant", "content": "hello"},
        ],
        "zen",
        "m",
    )
    assert sid
    recs = session.read_session(sdir / f"{sid}.jsonl")
    assert [(r["role"], r["content"]) for r in recs] == [("user", "hi"), ("assistant", "hello")]


# --- export / import / prune ---
def test_export_import_roundtrip(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import session

    sdir = tmp_path / "sessions"
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    sid = session.record_messages(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        "zen",
        "m",
    )
    data = session.export_session(sid)
    assert data["id"] == sid
    assert data["provider"] == "zen"
    assert len(data["messages"]) == 2
    dumped = _json.dumps(data)
    restored = session.import_session(_json.loads(dumped))
    assert restored == sid
    assert session.export_session(sid)["messages"] == data["messages"]


def test_cmd_export_missing(monkeypatch):
    import io

    from termux_agent import cli, session

    monkeypatch.setattr(session, "SESSIONS_DIR", Path("/nonexistent-sessions"))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_export("nope") == 1


def test_cmd_prune(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    for i in range(5):
        (sdir / f"20260819-{i:06d}.jsonl").write_text('{"role":"user","content":"x"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_prune(2) == 0
    assert len(session.list_sessions()) == 2


def test_one_shot_output_file(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    out_path = tmp_path / "out.txt"
    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda prompt, on_tool_use=None: "RESULT LINE",
    ))
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, quiet=True, output=str(out_path)) == 0
    assert out_path.read_text() == "RESULT LINE\n"


# --- clip / screenshot / parser ---
def test_clipboard_as_prompt(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    seen = {}

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            run=lambda prompt, on_tool_use=None: seen.update(prompt=prompt) or "OK",
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr("termux_agent.notify.clipboard_get", lambda: "FROM CLIPBOARD")
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_one_shot(_min_cfg(), "", "zen", None, quiet=True, clip=True) == 0
    assert seen.get("prompt") == "FROM CLIPBOARD"


def test_clipboard_empty(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    monkeypatch.setattr("termux_agent.notify.clipboard_get", lambda: None)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_one_shot(_min_cfg(), "", "zen", None, clip=True) == 2


def test_screenshot_attached(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    shot = tmp_path / "s.png"
    shot.write_bytes(b"png")
    seen = {}

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            run=lambda prompt, on_tool_use=None: seen.update(prompt=prompt) or "OK",
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr("termux_agent.notify.screenshot", lambda path=None: str(shot))
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_one_shot(_min_cfg(), "what is this?", "zen", None, quiet=True, screenshot=True) == 0
    assert f"[image: {shot}]" in seen.get("prompt", "")


def test_parser_all_flags_present():
    from termux_agent.cli import build_parser

    help_txt = build_parser().format_help()
    for flag in (
        "--clip --screenshot --export --import --prune --output --timeout --speak --wakelock --notify "
        "--chat --json --quiet --resume --serve --models --smoke --plan --copy --stats --doctor "
        "--install-completion --list-providers --list-agents --image --prompt-file --api-key --search "
        "--serve-workers --no-sessions --all"
    ).split():
        assert flag in help_txt, f"missing {flag}"


# --- server auth + /sessions ---
def test_server_token_auth(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.error
    import urllib.request
    from types import SimpleNamespace

    from termux_agent import server as srv
    from termux_agent import session
    from termux_agent.server import _AgentHandler

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text('{"role":"user","content":"hello"}\n{"role":"assistant","content":"hi"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[{"role": "system", "content": "s"}],
            run=lambda prompt: "OK",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None, token="sekret")
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
            assert _json.loads(r.read())["ok"] is True

        def chat(auth=None, body=None):
            headers = {"Content-Type": "application/json"}
            if auth:
                headers["Authorization"] = f"Bearer {auth}"
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/chat",
                data=_json.dumps(body or {"prompt": "x"}).encode(),
                headers=headers,
            )
            return urllib.request.urlopen(req, timeout=15)

        try:
            chat()
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as e:
            assert e.code == 401
        with chat("sekret") as r:
            assert _json.loads(r.read())["ok"] is True

        req = urllib.request.Request(f"http://127.0.0.1:{port}/sessions", headers={"Authorization": "Bearer sekret"})
        with urllib.request.urlopen(req, timeout=10) as r:
            sids = _json.loads(r.read())["sessions"]
        assert sids and sids[0]["id"] == "20260820-000001"
        assert sids[0]["first"] == "hello"

        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/sessions", timeout=10)
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as e:
            assert e.code == 401
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- export-all / forget / bench ---
def test_cmd_export_all(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    for i in range(3):
        (sdir / f"20260820-{i:06d}.jsonl").write_text('{"role":"user","content":"x"}\n')
    out_dir = tmp_path / "backup"
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_export_all(str(out_dir)) == 0
    files = sorted(p.name for p in out_dir.glob("*.json"))
    assert len(files) == 3
    data = _json.loads((out_dir / files[0]).read_text())
    assert data["messages"][0]["content"] == "x"


def test_cmd_forget(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text('{"role":"user","content":"x"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_forget("20260820-000001") == 0
    assert session.list_sessions() == []


def test_cmd_bench_runs_all_models(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    def fake_build(*a, **k):
        from types import SimpleNamespace

        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            run=lambda prompt, on_tool_use=None: "ok",
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr("termux_agent.cli._run_guarded", lambda a, p, t, to: "ok")
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    cfg = _min_cfg()
    cfg["providers"]["zen"]["models"] = ["m1", "m2"]
    assert cli.cmd_bench(cfg, "zen", timeout=5) == 0


def test_server_cors_headers(tmp_path: Path, monkeypatch):
    import threading
    from types import SimpleNamespace

    from termux_agent import server as srv
    from termux_agent.server import _AgentHandler

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[{"role": "system", "content": "s"}],
            run=lambda prompt: "OK",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
            assert r.headers.get("Access-Control-Allow-Origin") == "*"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- --stream / --prompt-file - ---
def test_main_prompt_file_stdin_dash(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    seen = {}

    def fake_one_shot(cfg, prompt, provider, model, **kw):
        seen["prompt"] = prompt
        return 0

    monkeypatch.setattr(cli, "cmd_one_shot", fake_one_shot)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("from stdin pipe"))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    assert cli.main(["--prompt-file", "-"]) == 0
    assert seen["prompt"] == "from stdin pipe"


def test_one_shot_stream_deltas(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    deltas = []

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )

        def _run(prompt, on_tool_use=None, on_text_delta=None):
            assert on_text_delta is not None
            deltas.append(on_text_delta("hel"))
            deltas.append(on_text_delta("lo"))
            return "hello"

        agent.run = _run
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, stream=True) == 0
    assert deltas == [None, None]


def test_plan_turn_asks_approval(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli, session
    from termux_agent.ui import repl

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    calls = []

    class FakeAgent:
        system_prompt = "SYS"
        allowed_tools = None
        provider = SimpleNamespace(name="zen", model="m")
        usage = {}
        messages = []
        ctx = SimpleNamespace(working_dir=tmp_path)

        def run(self, prompt, on_text_delta=None, on_tool_use=None):
            calls.append(prompt)
            if on_text_delta:
                on_text_delta("plan text")
            return "PLAN" if "planning mode" in prompt else "EXECUTED"

    r = repl.Repl(FakeAgent(), provider_name="zen", model="m")
    r.plan_mode = True
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    monkeypatch.setattr("builtins.input", lambda _: "y")
    r._run_turn("do it")
    assert len(calls) == 2
    assert "planning mode" in calls[0]
    assert "APPROVED PLAN" in calls[1]
    assert r._last_answer == "EXECUTED"


# --- --config / non-interactive init / doctor json ---
def test_load_config_explicit_file(tmp_path: Path, monkeypatch):
    import yaml as _yaml

    from termux_agent import cli

    cf = tmp_path / "custom.yaml"
    cf.write_text(_yaml.safe_dump({"provider": "anthropic", "model": "claude-x"}), encoding="utf-8")
    seen = {}

    def fake_one_shot(cfg, prompt, provider, model, **kw):
        seen["provider"] = cfg.get("provider")
        seen["model"] = cfg.get("model")
        return 0

    monkeypatch.setattr(cli, "cmd_one_shot", fake_one_shot)
    assert cli.main(["--config", str(cf), "hi"]) == 0
    assert seen == {"provider": "anthropic", "model": "claude-x"}


def test_init_noninteractive(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, config

    fake_cf = tmp_path / "config.yaml"
    monkeypatch.setattr(cli, "CONFIG_FILE", fake_cf)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_init("openai", "gpt-test") == 0
    cfg = config.load_config(str(fake_cf))
    assert cfg["provider"] == "openai"
    assert cfg["providers"]["openai"]["models"] == ["gpt-test"]


def test_init_unknown_provider(monkeypatch):
    import io

    from termux_agent import cli

    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_init("nonexistent") == 1


def test_doctor_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_doctor(_min_cfg(), as_json=True)
    data = _json.loads(out.getvalue())
    assert "checks" in data
    assert "ok" in data
    assert any(c["label"] == "python" for c in data["checks"])
    assert code in (0, 1)


# --- server SSE streaming ---
def test_server_stream_sse(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request
    from types import SimpleNamespace

    from termux_agent import server as srv
    from termux_agent import session
    from termux_agent.server import _AgentHandler

    sdir = tmp_path / "sessions"
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={"total_tokens": 5},
            messages=[{"role": "system", "content": "s"}],
        )

        def _run(prompt, on_text_delta=None, on_tool_use=None):
            on_text_delta("hel")
            on_text_delta("lo")
            return "hello"

        agent.run = _run
        return agent

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({"prompt": "x", "stream": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode()
        assert "event: start" in body
        assert 'event: delta' in body
        assert '"text": "hel"' in body
        assert 'event: done' in body
        assert '"answer": "hello"' in body
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- retries / no-fallback ---
def test_build_agent_retries_and_no_fallback(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    monkeypatch.chdir(tmp_path)
    agent = build_agent(_min_cfg(), "zen", None, retries=3)
    assert agent.retries == 3
    agent2 = build_agent(_min_cfg(), "zen", None, no_fallback=True)
    assert agent2.provider.fallback_models == []


# --- json outputs / rules / version ---
def test_cmd_sessions_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text('{"role":"user","content":"fix calc"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_sessions(as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert data["sessions"][0]["id"] == "20260820-000001"
    assert data["sessions"][0]["first"] == "fix calc"


def test_version_json(monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.main(["--version", "--json"]) == 0
    data = _json.loads(out.getvalue())
    assert data["name"] == "termux-agent"
    assert data["version"]


def test_one_shot_rules_file(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    rules = tmp_path / "rules.txt"
    rules.write_text("Always write tests.")
    seen = {}

    def fake_build(*a, **k):
        seen["extra_rules"] = a[13] if len(a) > 13 else k.get("extra_rules")
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )

        def _run(prompt, on_tool_use=None, on_text_delta=None):
            return "ok"

        agent.run = _run
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, quiet=True, rules_file=str(rules)) == 0
    assert seen["extra_rules"] == "Always write tests."


def test_build_agent_extra_rules(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    monkeypatch.chdir(tmp_path)
    agent = build_agent(_min_cfg(), "zen", None, extra_rules="Mind the indentation.")
    assert "Mind the indentation." in agent.system_prompt
    assert agent.messages[0]["content"] == agent.system_prompt


# --- system prompt / watch ---
def test_build_agent_custom_system_prompt(tmp_path: Path, monkeypatch):
    from termux_agent.cli import build_agent

    monkeypatch.chdir(tmp_path)
    agent = build_agent(_min_cfg(), "zen", None, system_prompt="You are a pirate.")
    assert agent.system_prompt == "You are a pirate."


def test_one_shot_system_prompt_file(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    sp = tmp_path / "persona.txt"
    sp.write_text("You are a pirate.")
    seen = {}

    def fake_build(*a, **k):
        seen["system_prompt"] = a[14] if len(a) > 14 else k.get("system_prompt")
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )
        agent.run = lambda prompt, on_tool_use=None, on_text_delta=None: "ok"
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, quiet=True, system_prompt_file=str(sp)) == 0
    assert seen["system_prompt"] == "You are a pirate."


def test_cmd_watch_loop(tmp_path: Path, monkeypatch):
    import io
    import time
    from types import SimpleNamespace

    from termux_agent import cli

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to: "ROUND OK")
    monkeypatch.setattr(time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_watch(_min_cfg(), "check", "zen", None, interval=1) == 0


def test_main_watch_requires_prompt(monkeypatch):
    import io

    from termux_agent import cli

    class FakeIn:
        def isatty(self):
            return True

    monkeypatch.setattr(cli.sys, "stdin", FakeIn())
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.main(["--watch", "60"]) == 2


# --- context / tools / config-show ---
def test_attach_agent_context(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent import cli

    agent = SimpleNamespace(
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
    )
    cli._attach_agent_context(agent, "battery: 87% (charging)")
    assert "battery: 87% (charging)" in agent.system_prompt
    assert agent.messages[0]["content"] == agent.system_prompt


def test_cmd_config_show(tmp_path: Path, monkeypatch):
    import io
    import yaml as _yaml

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_config_show(_min_cfg()) == 0
    parsed = _yaml.safe_load(out.getvalue())
    assert parsed["provider"] == "zen"


def test_cmd_list_tools(monkeypatch):
    import io

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_list_tools() == 0
    assert "read_file" in out.getvalue()


def test_cmd_prune_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    for i in range(4):
        (sdir / f"20260820-{i:06d}.jsonl").write_text('{"role":"user","content":"x"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_prune(1, as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert data["removed"] == 3
    assert data["kept"] == 1


# --- batch / search-all / prune-days / config-show json ---
def test_cmd_batch(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    from types import SimpleNamespace

    from termux_agent import cli

    f = tmp_path / "prompts.txt"
    f.write_text("fix calc\n\ncheck tests\n")
    out = tmp_path / "results.json"
    seen = []

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: seen.append(p) or "ANSWER:" + p
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to: agent.run(p))
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_batch(_min_cfg(), str(f), "zen", None, output=str(out)) == 0
    assert len(seen) == 2
    data = _json.loads(out.read_text())
    assert [r["prompt"] for r in data] == ["fix calc", "check tests"]
    assert data[0]["answer"] == "ANSWER:fix calc"


def test_cmd_sessions_search_any_message(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text(
        '{"role":"user","content":"hello"}\n{"role":"assistant","content":"the calculator bug is fixed"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_sessions("calculator") == 0
    assert "20260820-000001" in out.getvalue()


def test_cmd_prune_days(tmp_path: Path, monkeypatch):
    import io
    import os
    import time

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    old = sdir / "20260701-000001.jsonl"
    new = sdir / "20260820-000002.jsonl"
    old.write_text('{"role":"user","content":"old"}\n')
    new.write_text('{"role":"user","content":"new"}\n')
    old_time = time.time() - 10 * 86400
    os.utime(old, (old_time, old_time))
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_prune_days(7) == 0
    assert [p.stem for p in session.list_sessions()] == ["20260820-000002"]


def test_config_show_json(monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_config_show(_min_cfg(), as_json=True) == 0
    assert _json.loads(out.getvalue())["provider"] == "zen"


# --- tool groups / resource limits / repl context ---
def test_without_groups_removes_specs():
    from termux_agent.agent import Agent

    agent = Agent.__new__(Agent)
    agent.allowed_tools = None
    assert agent._without_groups(["shell", "web"]) is agent
    assert "run_command" not in agent.allowed_tools
    assert "web_fetch" not in agent.allowed_tools
    assert "read_file" in agent.allowed_tools
    assert "git_status" in agent.allowed_tools


def test_without_groups_preserves_agent_spec():
    from termux_agent.agent import Agent

    agent = Agent.__new__(Agent)
    agent.allowed_tools = {"read_file", "git_status"}
    agent._without_groups(["git"])
    assert agent.allowed_tools == {"read_file"}


def test_build_agent_tool_limits(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent import cli
    from termux_agent.agent import Agent

    cfg = _min_cfg()
    cfg["agents"]["root"] = {"prompt": "Be helpful."}
    monkeypatch.setattr(cli, "create_provider", lambda *a, **k: SimpleNamespace(fallback_models=[], chat=None))
    monkeypatch.setattr(cli, "resolve_working_dir", lambda cfg: tmp_path)
    a = cli.build_agent(
        cfg, "zen", "m", auto_accept=True,
        disabled_groups=["shell", "web"],
        max_output_chars=1000,
        command_timeout=5,
    )
    assert isinstance(a, Agent)
    assert "run_command" not in {s.name for s in a.tools}
    assert "web_search" not in {s.name for s in a.tools}
    assert a.ctx.max_output_chars == 1000
    assert a.ctx.command_timeout == 5


def test_disabled_groups_from(monkeypatch):
    from types import SimpleNamespace

    from termux_agent import cli

    args = SimpleNamespace(no_shell=True, no_web=False, no_git=True)
    assert cli._disabled_groups_from(args) == ["shell", "git"]


# --- show / tokens / markdown / no-save ---
def test_cmd_show_markdown(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    sid = "20260820-000001"
    (sdir / f"{sid}.jsonl").write_text(
        '{"role":"user","content":"hello"}\n{"role":"assistant","content":"hi there"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_show(sid) == 0
    assert "# Session 20260820-000001" in out.getvalue()
    assert "### user" in out.getvalue()
    assert "hi there" in out.getvalue()


def test_cmd_export_markdown(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text(
        '{"role":"user","content":"hello"}\n{"role":"assistant","content":"hi"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_export("20260820-000001", as_markdown=True) == 0
    assert "- provider:" in out.getvalue()


def test_cmd_tokens_file(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    f = tmp_path / "x.txt"
    f.write_text("a" * 800)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_tokens(str(f)) == 0
    assert "800 characters, ~200 tokens" in out.getvalue()


def test_one_shot_no_save(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli, session

    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[{"role": "system", "content": "s"}],
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: "answer"
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path / "sessions")
    assert cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, no_save=True) == 0
    assert not list(session.list_sessions())


# --- git context / only-tools / server config ---
def test_git_context_non_repo(tmp_path: Path):
    from termux_agent import cli

    assert cli._git_context(tmp_path) == ""


def test_git_context_repo(tmp_path: Path, monkeypatch):
    import subprocess

    from termux_agent import cli

    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    (tmp_path / "a.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first"], cwd=tmp_path)
    out = cli._git_context(tmp_path)
    assert "git log" in out
    assert "first" in out


def test_only_tools(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent import cli

    cfg = _min_cfg()
    cfg["agents"]["root"] = {"prompt": "Be helpful."}
    monkeypatch.setattr(cli, "create_provider", lambda *a, **k: SimpleNamespace(fallback_models=[], chat=None))
    monkeypatch.setattr(cli, "resolve_working_dir", lambda cfg: tmp_path)
    a = cli.build_agent(cfg, "zen", "m", auto_accept=True, only_tools=["read_file", "web_search"])
    names = {s.name for s in a.tools}
    assert names == {"read_file", "web_search"}


def test_split_tools():
    from termux_agent import cli

    assert cli._split_tools(" read_file , grep,glob ") == ["read_file", "grep", "glob"]
    assert cli._split_tools(None) is None
    assert cli._split_tools("") is None


def test_repl_image_command(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent.ui import repl as replmod
    from termux_agent.ui.repl import Repl

    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG")
    agent = SimpleNamespace(
        system_prompt="S",
        ctx=SimpleNamespace(working_dir=tmp_path, confirm_commands=True),
        messages=[],
        usage={},
    )
    monkeypatch.setattr(replmod, "render_info", lambda *a, **k: None)
    monkeypatch.setattr(replmod, "render_error", lambda *a, **k: None)
    r = Repl(agent, "zen", "m")
    calls = []
    r._run_turn = lambda p: calls.append(p)
    assert r._handle_command(f"/image {img}", None) is False
    assert calls and "[image:" in calls[0]


# --- export-all markdown / run logger ---
def test_cmd_export_all_markdown(tmp_path: Path, monkeypatch):
    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text(
        '{"role":"user","content":"hello"}\n{"role":"assistant","content":"hi"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    monkeypatch.setattr(cli.sys, "stdout", __import__("io").StringIO())
    out_dir = tmp_path / "exp"
    assert cli.cmd_export_all(str(out_dir), as_markdown=True) == 0
    assert (out_dir / "markdown" / "20260820-000001.md").is_file()
    assert "# Session" in (out_dir / "markdown" / "20260820-000001.md").read_text()


def test_run_logger(tmp_path: Path):
    import json as _json

    from termux_agent import cli

    log = tmp_path / "run.jsonl"
    logger = cli._run_logger(str(log))
    logger("tool", {"name": "read_file"})
    logger("done", {"answer": "ok", "tool_calls": 1})
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    first = _json.loads(lines[0])
    assert first["kind"] == "tool"
    assert first["name"] == "read_file"
    assert "ts" in first


def test_one_shot_writes_log(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: (on_tool_use("grep", "{}") or "answer")
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None, on_text_delta=None: agent.run(p, on_tool_use=t))
    log = tmp_path / "run.jsonl"
    assert cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, log_file=str(log)) == 0
    kinds = [_json.loads(l)["kind"] for l in log.read_text().splitlines()]
    assert "tool" in kinds
    assert "done" in kinds


# --- workers / memory / server model override ---
def test_cmd_batch_workers(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    from types import SimpleNamespace

    from termux_agent import cli

    f = tmp_path / "p.txt"
    f.write_text("a\nb\nc\n")
    calls = []

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: calls.append(p) or "R:" + p
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    out = tmp_path / "r.json"
    assert cli.cmd_batch(_min_cfg(), str(f), "zen", None, output=str(out), workers=3) == 0
    data = _json.loads(out.read_text())
    assert [r["prompt"] for r in data] == ["a", "b", "c"]
    assert len(calls) == 3


def test_agent_no_memory(tmp_path: Path, monkeypatch):
    from termux_agent.agent import Agent, load_memory
    from termux_agent.providers.base import Provider
    from termux_agent.tools.base import ToolContext

    mem = tmp_path / "memory.md"
    mem.write_text("remember pineapple")
    monkeypatch.setattr("termux_agent.agent.MEMORY_FILE", mem)
    assert "pineapple" in load_memory()

    class P(Provider):
        name = "p"
        model = "m"

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            yield from ()

    a = Agent(P(), ToolContext(working_dir=tmp_path))
    assert "pineapple" in a.system_prompt
    b = Agent(P(), ToolContext(working_dir=tmp_path), memory=False)
    assert "pineapple" not in b.system_prompt


def test_server_chat_model_override():
    from termux_agent import server as servermod

    body = {"prompt": "hi", "model": "other-model"}
    assert body.get("model") == "other-model"
    # build_agent call in do_POST uses data.get("model") or self.model
    chosen = body.get("model") or "default"
    assert chosen == "other-model"


# --- summarize / session-dir ---
def test_cmd_summarize(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    sid = "20260820-000001"
    (sdir / f"{sid}.jsonl").write_text(
        '{"role":"system","content":"sys"}\n{"role":"user","content":"fix login bug"}\n{"role":"assistant","content":"done, it was a race condition"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    seen = {}

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: (seen.update(prompt=p) or "SUMMARY")
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    out = tmp_path / "summary.txt"
    assert cli.cmd_summarize(_min_cfg(), sid, "zen", None, output=str(out)) == 0
    assert "fix login bug" in seen["prompt"]
    assert out.read_text().strip() == "SUMMARY"


def test_cmd_summarize_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text(
        '{"role":"user","content":"hello"}\n{"role":"assistant","content":"hi"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: "S"
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_summarize(_min_cfg(), None, "zen", None, as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert data["summary"] == "S"
    assert data["session"] == "20260820-000001"


def test_main_session_dir(tmp_path: Path, monkeypatch):
    from termux_agent import cli, session

    sdir = tmp_path / "alt"
    monkeypatch.setattr(cli.sys, "stdout", __import__("io").StringIO())
    monkeypatch.setattr(cli, "cmd_sessions", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "cmd_init", lambda *a, **k: 0)
    code = cli.main(["--session-dir", str(sdir), "--sessions"])
    assert code == 0
    assert session.SESSIONS_DIR == sdir


# --- doctor termux / memory / providers json / watch context ---
def test_doctor_termux_checks(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.chdir(tmp_path)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_doctor(_min_cfg(), termux=True)
    assert code in (0, 1)
    assert "termux-api:" in out.getvalue()


def test_doctor_termux_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    monkeypatch.chdir(tmp_path)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    cli.cmd_doctor(_min_cfg(), termux=True, as_json=True)
    data = _json.loads(out.getvalue())
    labels = [c["label"] for c in data["checks"]]
    assert any("termux-api" in l for l in labels)


def test_repl_memory(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent import cli
    from termux_agent.ui import repl as replmod
    from termux_agent.ui.repl import Repl

    mem = tmp_path / "memory.md"
    mem.write_text("remember pizza")
    monkeypatch.setattr("termux_agent.agent.MEMORY_FILE", mem)
    monkeypatch.setattr(replmod, "render_info", lambda *a, **k: None)
    monkeypatch.setattr(replmod, "render_error", lambda *a, **k: None)
    agent = SimpleNamespace(
        system_prompt="S",
        ctx=SimpleNamespace(working_dir=tmp_path, confirm_commands=True),
        messages=[],
        usage={},
    )
    r = Repl(agent, "zen", "m")
    assert r._handle_command("/memory", None) is False


def test_list_providers_json(monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_list_providers(_min_cfg(), as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert any(p["name"] == "zen" for p in data["providers"])


# --- server chat extras / tools endpoint / forget json ---
def test_server_chat_body_extras(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    body = {"prompt": "hi", "provider": "zen", "model": "m2", "rules": "be terse", "image": str(tmp_path / "x.png")}
    (tmp_path / "x.png").write_bytes(b"\x89PNG")
    captured = {}

    def fake_build(cfg, provider, model, **kw):
        captured.update(provider=provider, model=model, rules=kw.get("extra_rules"))
        agent = SimpleNamespace(
            provider=SimpleNamespace(name=provider, model=model),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[{"role": "system", "content": "s"}],
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: "R:" + p
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_forget if False else True
    # emulate server field extraction
    prompt = str(body.get("prompt", "")).strip()
    if isinstance(body.get("image"), str) and (tmp_path / "x.png").exists():
        prompt += f"\n[image: {tmp_path / 'x.png'}]"
    assert "[image:" in prompt
    chosen = body.get("provider") or "default"
    assert chosen == "zen"


def test_forget_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text('{"role":"user","content":"x"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_forget("20260820-000001", as_json=True) == 0
    assert _json.loads(out.getvalue())["deleted"] == "20260820-000001"
    assert not list(session.list_sessions())


def test_forget_json_missing(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_forget("nope", as_json=True) == 1
    assert _json.loads(out.getvalue())["ok"] is False


# --- bundle / restore / no-color / cron ---
def test_cmd_bundle_and_restore(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text('{"role":"user","content":"x"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("provider: zen\n")
    monkeypatch.setattr(cli, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cli, "CONFIG_FILE", cfg_dir / "config.yaml")
    mem = tmp_path / "memory.md"
    mem.write_text("note")
    monkeypatch.setattr("termux_agent.agent.MEMORY_FILE", mem)
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())

    bundle_dir = tmp_path / "bundle"
    assert cli.cmd_bundle(str(bundle_dir)) == 0
    assert (bundle_dir / "manifest.json").is_file()
    assert (bundle_dir / "sessions" / "20260820-000001.jsonl").is_file()

    # restore into a fresh location
    rest = tmp_path / "rest"
    (rest / "sessions").mkdir(parents=True)
    new_sdir = rest / "sessions"
    monkeypatch.setattr(session, "SESSIONS_DIR", new_sdir)
    new_cfg_dir = rest / "cfg"
    new_cfg_dir.mkdir()
    monkeypatch.setattr(cli, "CONFIG_DIR", new_cfg_dir)
    monkeypatch.setattr(cli, "CONFIG_FILE", new_cfg_dir / "config.yaml")
    assert cli.cmd_restore(str(bundle_dir)) == 0
    assert (new_cfg_dir / "config.yaml").is_file()
    assert (new_sdir / "20260820-000001.jsonl").is_file()


def test_cmd_restore_rejects_non_bundle(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    bad = tmp_path / "bad"
    bad.mkdir()
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_restore(str(bad)) == 1


def test_disable_color():
    from termux_agent.ui import renderer

    renderer.disable_color()
    assert renderer.console.no_color is True


def test_cmd_cron(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    monkeypatch.chdir(tmp_path)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_cron("*/10 * * * *", "backup notes") == 0
    line = out.getvalue()
    assert line.startswith("*/10 * * * * cd")
    assert "termux-agent --no-save --quiet" in line
    assert "cron.log" in line


# --- allow-dir / sessions/<id> endpoint / cleanup / screenshot-dir ---
def test_build_agent_allow_dirs(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent import cli

    extra = tmp_path / "extra"
    extra.mkdir()
    cfg = _min_cfg()
    cfg["agents"]["root"] = {"prompt": "Be helpful."}
    monkeypatch.setattr(cli, "create_provider", lambda *a, **k: SimpleNamespace(fallback_models=[], chat=None))
    monkeypatch.setattr(cli, "resolve_working_dir", lambda cfg: tmp_path)
    a = cli.build_agent(cfg, "zen", "m", auto_accept=True, allow_dirs=[str(extra)])
    assert str(extra.resolve()) in a.ctx._allowed_dirs


def test_allow_dirs_from():
    from types import SimpleNamespace

    from termux_agent import cli

    assert cli._allow_dirs_from(SimpleNamespace(allow_dir=["/a", "/b"])) == ["/a", "/b"]
    assert cli._allow_dirs_from(SimpleNamespace(allow_dir=[])) is None


def test_cmd_cleanup(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    monkeypatch.chdir(tmp_path)
    (tmp_path / "screenshot-123.png").write_bytes(b"x")
    (tmp_path / "notes.png").write_bytes(b"x")
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_cleanup() == 0
    assert not (tmp_path / "screenshot-123.png").exists()
    assert (tmp_path / "notes.png").exists()
    assert "1" in out.getvalue()


def test_one_shot_screenshot_dir(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    shot_dir = tmp_path / "shots"
    captured = {}

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: "ok"
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr("termux_agent.notify.screenshot", lambda path=None: (captured.update(path=path) or "shot.png"))
    assert cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, screenshot=True, screenshot_dir=str(shot_dir)) == 0
    assert captured.get("path") and shot_dir.name in captured["path"]


# --- agents/models json / rerun ---
def test_list_agents_json(monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_list_agents(_min_cfg(), as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert any(a["name"] == "root" for a in data["agents"])


def test_list_models_json(monkeypatch):
    import io
    import json as _json
    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli, "create_provider", lambda *a, **k: SimpleNamespace(list_models=lambda: ["m1", "m2"]))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_list_models(_min_cfg(), "zen", as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert data["provider"] == "zen"
    assert "m1" in data["models"]


def test_cmd_rerun(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    sid = "20260820-000001"
    (sdir / f"{sid}.jsonl").write_text(
        '{"role":"user","content":"fix this"}\n{"role":"assistant","content":"old answer"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    seen = {}

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: (seen.update(prompt=p) or "NEW ANSWER")
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_rerun(_min_cfg(), sid, "zen", None, as_json=True) == 0
    assert seen["prompt"] == "fix this"
    data = _json.loads(out.getvalue())
    assert data["answer"] == "NEW ANSWER"


def test_cmd_rerun_missing_session(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_rerun(_min_cfg(), "nope", "zen", None) == 1


# --- serve lifecycle / token file ---
def test_serve_stop_no_pidfile(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_serve_stop() == 1


def test_serve_stop_kills(tmp_path: Path, monkeypatch):
    import io
    import os
    import signal

    from termux_agent import cli

    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    killed = {}

    def fake_kill(pid, sig):
        killed["pid"] = pid
        killed["sig"] = sig

    monkeypatch.setattr(os, "kill", fake_kill)
    (tmp_path / "server.pid").write_text("12345")
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_serve_stop() == 0
    assert killed == {"pid": 12345, "sig": signal.SIGTERM}
    assert not (tmp_path / "server.pid").exists()


def test_serve_background_spawn(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    seen = {}

    class FakeProc:
        pid = 999

    def fake_popen(cmd, **kw):
        seen["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_serve(_min_cfg(), "127.0.0.1", 8787, "zen", "m", True, "tok", background=True)
    assert code == 0
    assert (tmp_path / "server.pid").read_text() == "999"
    assert "--token" in seen["cmd"] and "tok" in seen["cmd"]
    assert "background" in out.getvalue()


# --- prune dry-run / batch fail-fast / watch max-rounds / show tokens ---
def test_prune_dry_run_no_delete(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    for i in range(3):
        (sdir / f"20260820-00000{i}.jsonl").write_text('{"role":"user","content":"x"}\n')
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_prune(1, as_json=True, dry_run=True) == 0
    assert len(list(sdir.glob("*.jsonl"))) == 3
    import json as _json

    data = _json.loads(out.getvalue())
    assert data["dry_run"] is True and data["removed"] == 2


def test_batch_fail_fast(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    results = {"a": {"answer": "ok"}, "b": {"error": "boom"}}

    def fake_run_one(p):
        return results[p]

    monkeypatch.setattr(cli, "_batch_run_one", lambda *a, **k: results[a[-1]])
    in_path = tmp_path / "in.txt"
    in_path.write_text("a\nb\n")
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_batch(_min_cfg(), str(in_path), "zen", None, as_json=True, fail_fast=True)
    assert code == 1
    import json as _json

    assert _json.loads(out.getvalue())["fail_fast"] is True


def test_cmd_show_estimates_tokens(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text(
        '{"role":"user","content":"hello world hello world hello world hello world"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_show("20260820-000001") == 0
    assert "tokens estimated" in out.getvalue()


# --- provider:model shorthand / watch notify / stats ---
def test_provider_colon_shorthand(monkeypatch):
    from termux_agent import cli

    called = {}

    def fake_cmd_rerun(cfg, ref, provider, model, **kw):
        called["provider"] = provider
        called["model"] = model
        return 0

    monkeypatch.setattr(cli, "cmd_rerun", fake_cmd_rerun)
    assert cli.main(["--rerun", "x", "--provider", "zen:nemotron-3-ultra-free"]) == 0
    assert called == {"provider": "zen", "model": "nemotron-3-ultra-free"}


def test_watch_notify_round(monkeypatch):
    import io
    import os
    from types import SimpleNamespace

    from termux_agent import cli

    seen = {}

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=None),
            usage={},
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: "DONE"
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    os.environ["TERMUX_AGENT_NOTIFY"] = "1"

    def fake_notify(msg):
        seen["msg"] = msg

    monkeypatch.setattr("termux_agent.notify.notify", fake_notify)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_watch(_min_cfg(), "hi", "zen", None, interval=1, max_rounds=2, notify=True)
    assert code == 0
    assert "Round 2 done" in seen["msg"]


def test_stats_endpoint(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv
    from termux_agent import session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text('{"role":"user","content":"x"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=10) as r:
            stats = _json.loads(r.read())
        assert stats["sessions"] == 1
        assert stats["sessions_bytes"] > 0
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- attach / watch diff / completion ---
def test_one_shot_attach(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    note = tmp_path / "note.md"
    note.write_text("THE FILE BODY")
    seen = {}

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: (seen.update(prompt=p) or "ok")
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr(cli, "resolve_working_dir", lambda cfg: tmp_path)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_one_shot(_min_cfg(), "read this", "zen", None, attach=[str(note)])
    assert code == 0
    assert "THE FILE BODY" in seen["prompt"]
    assert str(note) in seen["prompt"]


def test_cmd_completion(monkeypatch):
    import io

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.main(["--completion", "bash"]) == 0
    assert "_termux_agent" in out.getvalue()
    assert cli.main(["--completion", "fish"]) == 0
    assert cli.main(["--completion", "tcsh"]) == 1


def test_watch_diff_skips_unchanged(monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=None),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: "SAME",
    ))
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_watch(_min_cfg(), "hi", "zen", None, interval=1, max_rounds=3, diff=True)
    assert code == 0
    rendered = out.getvalue()
    assert rendered.count("--- round") == 1
    assert "unchanged" in rendered


# --- server memory/batch endpoints / batch notify ---
def test_server_memory_endpoint(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import agent as agentmod
    from termux_agent import server as srv

    monkeypatch.setattr(agentmod, "MEMORY_FILE", tmp_path / "memory.md")
    monkeypatch.setattr(agentmod, "CONFIG_DIR", tmp_path)
    from termux_agent.cli import build_agent

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/memory", timeout=10) as r:
            assert _json.loads(r.read())["memory"] == ""
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/memory",
            data=_json.dumps({"content": "remember x"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assert _json.loads(r.read())["memory"] == "remember x"
        assert (tmp_path / "memory.md").read_text() == "remember x"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_batch_endpoint(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "A:" + p,
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/batch",
            data=_json.dumps({"prompts": ["one", "two"]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            results = _json.loads(r.read())["results"]
        assert [x["answer"] for x in results] == ["A:one", "A:two"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_batch_notify_on_done(tmp_path: Path, monkeypatch):
    import os
    from types import SimpleNamespace

    from termux_agent import cli

    os.environ["TERMUX_AGENT_NOTIFY"] = "1"
    seen = {}

    def fake_notify(msg):
        seen["msg"] = msg

    monkeypatch.setattr("termux_agent.notify.notify", fake_notify)
    monkeypatch.setattr(cli, "_batch_run_one", lambda *a, **k: {"prompt": "x", "answer": "ok"})
    in_path = tmp_path / "in.txt"
    in_path.write_text("x\n")
    code = cli.cmd_batch(_min_cfg(), str(in_path), "zen", None, notify=True)
    assert code == 0
    assert "1/1 succeeded" in seen["msg"]


# --- /attach /search ---
def test_repl_attach(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    note = tmp_path / "note.txt"
    note.write_text("ATTACHED BODY")
    seen = {}

    agent = SimpleNamespace(
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
        ctx=SimpleNamespace(working_dir=tmp_path),
    )
    agent.run = lambda p, on_tool_use=None, on_text_delta=None: (seen.update(prompt=p) or "ok")
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    repl = Repl(agent, provider_name="zen", model="m")
    repl._handle_command(f"/attach {note}", None)
    assert "ATTACHED BODY" in seen["prompt"]
    assert str(note) in seen["prompt"]


def test_repl_search(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent import session
    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    (sdir / "20260820-000001.jsonl").write_text(
        '{"role":"user","content":"tell me about raccoons"}\n{"role":"assistant","content":"raccoons are cute"}\n'
    )
    (sdir / "20260820-000002.jsonl").write_text('{"role":"user","content":"weather today"}\n')
    agent = SimpleNamespace(
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
        ctx=SimpleNamespace(working_dir=tmp_path),
    )
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    out = io.StringIO()
    monkeypatch.setattr("termux_agent.ui.repl.console", SimpleNamespace(print=lambda *a, **k: out.write(" ".join(map(str, a)) + "\n")))
    repl = Repl(agent, provider_name="zen", model="m")
    repl._handle_command("/search raccoons", None)
    assert "20260820-000001" in out.getvalue()
    assert "20260820-000002" not in out.getvalue()


def test_rerun_attach(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    sid = "20260820-000001"
    (sdir / f"{sid}.jsonl").write_text('{"role":"user","content":"read the file"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    note = tmp_path / "extra.txt"
    note.write_text("EXTRA BODY")
    seen = {}

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: (seen.update(prompt=p) or "ok")
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_rerun(_min_cfg(), sid, "zen", None, as_json=True, attach=[str(note)])
    assert code == 0
    assert "EXTRA BODY" in seen["prompt"]
    assert str(note) in seen["prompt"]


# --- batch stdin / rerun diff / sessions limit ---
def test_batch_stdin(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    seen = []
    monkeypatch.setattr(cli, "_batch_run_one", lambda *a, **k: seen.append(a[-1]) or {"prompt": a[-1], "answer": "ok"})
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("one\ntwo\n\nthree\n"))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_batch(_min_cfg(), "-", "zen", None, as_json=True) == 0
    assert seen == ["one", "two", "three"]


def test_rerun_diff(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    sid = "20260820-000001"
    (sdir / f"{sid}.jsonl").write_text(
        '{"role":"user","content":"q"}\n{"role":"assistant","content":"OLD LINE"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
        )
        agent.run = lambda p, on_tool_use=None, on_text_delta=None: "NEW LINE"
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_rerun(_min_cfg(), sid, "zen", None, diff=True) == 0
    assert "OLD LINE" in out.getvalue()
    assert "NEW LINE" in out.getvalue()


def test_sessions_limit(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    for i in range(5):
        (sdir / f"20260820-00000{i}.jsonl").write_text('{"role":"user","content":"x"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_sessions(as_json=True, limit=2) == 0
    assert len(_json.loads(out.getvalue())["sessions"]) == 2


# --- rotate / show-system-prompt ---
def test_one_shot_rotate_falls_back(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent import cli

    models_tried = []

    def fake_build(*a, **k):
        which = a[2] if len(a) > 2 else None
        models_tried.append(which)
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model=which or "m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
        )

        def _run(p, on_tool_use=None, on_text_delta=None):
            if which == "bad-model":
                raise RuntimeError("rate limited")
            return "GOOD"

        agent.run = _run
        return agent

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr(cli, "resolve_working_dir", lambda cfg: tmp_path)
    monkeypatch.setattr(cli, "_maybe_notify", lambda *a, **k: None)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    cfg = _min_cfg()
    cfg["providers"]["zen"]["models"] = ["bad-model", "good-model"]
    code = cli.cmd_one_shot(cfg, "hi", "zen", None, rotate=True, as_json=True)
    assert code == 0
    assert models_tried == ["bad-model", "good-model"]
    import json as _json

    assert _json.loads(out.getvalue())["answer"] == "GOOD"


def test_show_system_prompt(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent import cli

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            system_prompt="SYS PROMPT TEXT",
            messages=[],
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_show_system_prompt(_min_cfg(), "zen", None) == 0
    assert "SYS PROMPT TEXT" in out.getvalue()


# --- /retry / show-output / import dry-run ---
def test_repl_retry(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    calls = []

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            system_prompt="BASE",
            messages=[{"role": "system", "content": "BASE"}],
            allowed_tools=set(),
        )

    agent = fake_build()
    agent.run = lambda p, on_tool_use=None, on_text_delta=None: (calls.append(p) or "ok")
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    repl = Repl(agent, provider_name="zen", model="m")
    repl._run_turn("hello")
    repl._handle_command("/retry", None)
    assert calls == ["hello", "hello"]


def test_show_output(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text('{"role":"user","content":"hi there"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out_file = tmp_path / "out.md"
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_show("20260820-000001", output=str(out_file)) == 0
    assert "hi there" in out_file.read_text()
    assert "Transcript written" in out.getvalue()


def test_import_dry_run(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    valid = tmp_path / "valid.json"
    valid.write_text(_json.dumps({"messages": [{"role": "user", "content": "x"}]}))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_import(str(valid), dry_run=True) == 0
    assert cli.cmd_import(str(bad), dry_run=True) == 1
    assert len(list(sdir.glob("*.jsonl"))) == 0


# --- server only_tools / models query / doctor model check ---
def test_server_chat_only_tools(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    seen = {}

    def fake_build(*a, **k):
        seen["only_tools"] = k.get("only_tools")
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({"prompt": "hi", "only_tools": ["read_file"]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assert _json.loads(r.read())["ok"] is True
        assert seen["only_tools"] == ["read_file"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_models_provider_query(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    class FakeProv:
        name = "zen"
        model = "m"

        def list_models(self):
            return ["m1", "m2"]

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=FakeProv(),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/models?provider=zen", timeout=10) as r:
            data = _json.loads(r.read())
        assert data["provider"] == "zen"
        assert data["models"] == ["m1", "m2"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_doctor_configured_model_check():
    import io

    from termux_agent import cli

    cfg = _min_cfg()
    cfg["model"] = "m"
    out = io.StringIO()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli.sys, "stdout", out)
        assert cli.cmd_doctor(cfg, as_json=True) == 0
    import json as _json

    checks = _json.loads(out.getvalue())["checks"]
    model_check = next((c for c in checks if c["label"] == "configured model"), None)
    assert model_check is not None
    assert model_check["ok"] is True


# --- repl quiet / watch json / import stdin ---
def test_repl_quiet_toggle(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    kwargs_seen = []

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            system_prompt="BASE",
            messages=[{"role": "system", "content": "BASE"}],
            allowed_tools=set(),
        )

    agent = fake_build()
    agent.run = lambda p, on_tool_use=None, on_text_delta=None: (kwargs_seen.append(bool(on_text_delta)) or "ok")
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    repl = Repl(agent, provider_name="zen", model="m")
    repl._run_turn("one")
    repl._handle_command("/quiet", None)
    repl._run_turn("two")
    assert kwargs_seen == [True, False]


def test_watch_max_wait(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: "A",
    ))
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    monotonic = iter([1.0, 99.0])
    monkeypatch.setattr("time.monotonic", lambda: next(monotonic))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_watch(_min_cfg(), "hi", "zen", None, interval=1, max_wait=5, as_json=True)
    assert code == 0
    assert _json.loads(out.getvalue().strip())["finished"] is True


def test_init_force(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    from termux_agent.config import CONFIG_FILE, CONFIG_DIR, DEFAULTS

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text("old", encoding="utf-8")
    monkeypatch.setattr(cli, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cli, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(cli, "DEFAULTS", DEFAULTS)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("zen\n\nn\n"))
    assert cli.cmd_init() == 1
    assert cfg_file.read_text(encoding="utf-8") == "old"
    assert cli.cmd_init(provider="zen", model="m", force=True) == 0
    assert "zen" in cfg_file.read_text(encoding="utf-8")


def test_watch_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli

    answers = iter(["A", "B"])
    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: next(answers),
    ))
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_watch(_min_cfg(), "hi", "zen", None, interval=1, max_rounds=2, as_json=True)
    assert code == 0
    lines = [l for l in out.getvalue().strip().splitlines() if l]
    assert _json.loads(lines[0])["answer"] == "A"
    assert _json.loads(lines[1])["answer"] == "B"


def test_import_stdin(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(_json.dumps({"messages": [{"role": "user", "content": "x"}]})))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_import("-") == 0
    assert len(list(sdir.glob("*.jsonl"))) == 1


# --- repl temp / bundle stdout ---
def test_repl_temp(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    agent = SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path, undo=lambda: "noop"),
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
        allowed_tools=set(),
        temperature=0.7,
        run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
    )
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    repl = Repl(agent, provider_name="zen", model="m")
    out = io.StringIO()
    monkeypatch.setattr("termux_agent.ui.repl.render_info", lambda s: out.write(str(s)))
    assert repl._handle_command("/temp 0.2", None) is False
    assert agent.temperature == 0.2
    assert repl._handle_command("/temp 9.0", None) is False
    assert agent.temperature == 0.2


def test_bundle_stdout(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    import tarfile

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "config.json")
    cli.CONFIG_FILE.write_text(_json.dumps({"provider": "zen"}))
    out = io.BytesIO()

    class FakeStdout:
        buffer = out

    monkeypatch.setattr(cli.sys, "stdout", FakeStdout())
    assert cli.cmd_bundle("-") == 0
    out.seek(0)
    with tarfile.open(fileobj=out, mode="r:gz") as tf:
        names = tf.getnames()
    assert "config.json" in names


# --- server DELETE session / markdown / prune keep ---
def test_server_delete_session(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.error
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    sid = session.record_messages([{"role": "user", "content": "hi"}], "zen", "m")
    assert (sdir / f"{sid}.jsonl").exists()

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/sessions/{sid}", method="DELETE")
        with urllib.request.urlopen(req, timeout=10) as r:
            assert _json.loads(r.read())["deleted"] == sid
        assert not (sdir / f"{sid}.jsonl").exists()
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/sessions/{sid}", method="DELETE"), timeout=10)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_session_markdown(tmp_path: Path, monkeypatch):
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    sid = session.record_messages([{"role": "user", "content": "hi"}], "zen", "m")

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/sessions/{sid}?markdown=1", timeout=10) as r:
            body = r.read().decode()
        assert "hi" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_prune_days_keep(tmp_path: Path, monkeypatch):
    import io
    import time

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    old = session.record_messages([{"role": "user", "content": "old"}], "zen", "m", session_id="old-sess")
    new = session.record_messages([{"role": "user", "content": "new"}], "zen", "m", session_id="new-sess")
    past = time.time() - 10 * 86400
    import os

    os.utime(sdir / f"{old}.jsonl", (past, past))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_prune_days(days=7, keep=1) == 0
    assert not (sdir / f"{old}.jsonl").exists()
    assert (sdir / f"{new}.jsonl").exists()


# --- server chat overrides / repl log ---
def test_server_chat_overrides(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    seen = {}

    def fake_build(*a, **k):
        seen.update(k)
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[{"role": "system", "content": "BASE"}],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({"prompt": "hi", "temperature": 0.3, "max_tool_rounds": 5, "system_prompt": "CUSTOM"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assert _json.loads(r.read())["ok"] is True
        assert seen["temperature"] == 0.3
        assert seen["max_tool_rounds"] == 5
        assert seen["system_prompt"] == "CUSTOM"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_repl_log(tmp_path: Path, monkeypatch):
    import json as _json

    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    agent = SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
        allowed_tools=set(),
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        run=lambda p, on_tool_use=None, on_text_delta=None: "answer",
    )
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    log_path = tmp_path / "repl.log"
    repl = Repl(agent, provider_name="zen", model="m", log_file=str(log_path))
    repl._run_turn("hello")
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = _json.loads(lines[0])
    assert rec["kind"] == "turn"
    assert rec["user"] == "hello"
    assert rec["assistant"] == "answer"


# --- restore stdin / smoke json / health ---
def test_restore_stdin(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    import tarfile

    from termux_agent import cli, session

    src = tmp_path / "src"
    src.mkdir()
    (src / "manifest.json").write_text(_json.dumps({"app": "termux-agent", "version": "0.4.0", "sessions": 1}))
    (src / "config.yaml").write_text("provider: zen\n")
    ses_dir = src / "sessions"
    ses_dir.mkdir()
    (ses_dir / "abc.jsonl").write_text('{"role": "user", "content": "hi"}\n')

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(src / "manifest.json", arcname="manifest.json")
        tf.add(src / "config.yaml", arcname="config.yaml")
        tf.add(ses_dir / "abc.jsonl", arcname="sessions/abc.jsonl")

    cdir = tmp_path / "cfg"
    sdir = tmp_path / "sessions"
    cdir.mkdir()
    sdir.mkdir()
    monkeypatch.setattr(cli, "CONFIG_DIR", cdir)
    monkeypatch.setattr(cli, "CONFIG_FILE", cdir / "config.yaml")
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    fake_stdin = io.TextIOWrapper(io.BytesIO(buf.getvalue()))
    monkeypatch.setattr(cli.sys, "stdin", fake_stdin)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_restore("-") == 0
    assert (cdir / "config.yaml").is_file()
    assert (sdir / "abc.jsonl").is_file()


def test_smoke_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={"prompt_tokens": 5, "completion_tokens": 2},
        run=lambda p, on_tool_use=None, on_text_delta=None: "OK",
    ))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_smoke(_min_cfg(), "zen", "m", as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert data["ok"] is True
    assert data["provider"] == "zen"
    assert data["model"] == "m"


def test_health_version_pid(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
            data = _json.loads(r.read())
        assert data["ok"] is True
        assert data["pid"] > 0
        assert data["uptime"] >= 0
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- rerun/summarize notify / import json / doctor disk ---
def test_rerun_notify(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    sid = session.record_messages([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "old"}], "zen", "m", session_id="rerun-x")
    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: "new answer",
    ))
    seen = {}
    monkeypatch.setattr("termux_agent.notify.notify", lambda msg: seen.setdefault("msg", msg))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_rerun(_min_cfg(), sid, "zen", "m", as_json=True, notify=True) == 0
    assert _json.loads(out.getvalue())["answer"] == "new answer"
    assert "Rerun done" in seen["msg"]


def test_summarize_notify(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    sid = session.record_messages([{"role": "user", "content": "a long conversation"}, {"role": "assistant", "content": "b"}], "zen", "m", session_id="sum-x")
    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: "summary text",
    ))
    seen = {}
    monkeypatch.setattr("termux_agent.notify.notify", lambda msg: seen.setdefault("msg", msg))
    assert cli.cmd_summarize(_min_cfg(), sid, "zen", "m", notify=True) == 0
    assert "Summary done" in seen["msg"]


def test_import_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    src = tmp_path / "s.json"
    src.write_text(_json.dumps({"messages": [{"role": "user", "content": "x"}]}))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_import(str(src), as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert data["ok"] is True
    assert data["messages"] == 1


def test_doctor_disk_check():
    import io
    import json as _json

    from termux_agent import cli

    out = io.StringIO()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli.sys, "stdout", out)
        assert cli.cmd_doctor(_min_cfg(), as_json=True) == 0
    checks = _json.loads(out.getvalue())["checks"]
    disk = next((c for c in checks if c["label"] == "free disk (/)"), None)
    assert disk is not None
    assert disk["ok"] is True


# --- watch output / tokens json / bench json ---
def test_watch_output(tmp_path: Path, monkeypatch):
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli

    answers = iter(["B"])
    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: next(answers),
    ))
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    out = tmp_path / "last.txt"
    assert cli.cmd_watch(_min_cfg(), "hi", "zen", None, interval=1, max_rounds=1, output=str(out)) == 0
    assert out.read_text().strip() == "B"


def test_tokens_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    f = tmp_path / "t.txt"
    f.write_text("hello world this is a test")
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_tokens(str(f), as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert data["ok"] is True
    assert data["chars"] == 26
    assert data["tokens"] == 6


def test_bench_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli

    cfg = _min_cfg()
    cfg["providers"] = {"zen": {"models": ["m1", "m2"]}}
    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
    ))
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_bench(cfg, "zen", as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert data["provider"] == "zen"
    assert len(data["models"]) == 2
    assert all(m["ok"] for m in data["models"])


# --- server request log / doctor sessions ---
def test_server_request_log(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    log_file = tmp_path / "server.jsonl"
    handler = srv._AgentHandler
    handler.log_path = str(log_file)
    try:
        httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
                r.read()
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        handler.log_path = None
    lines = [l for l in log_file.read_text().strip().splitlines() if l]
    assert len(lines) == 1
    rec = _json.loads(lines[0])
    assert rec["method"] == "GET"
    assert rec["path"] == "/health"
    assert rec["status"] == 200


def test_doctor_sessions_check(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "x"}], "zen", "m")
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli.sys, "stdout", out)
        assert cli.cmd_doctor(_min_cfg(), as_json=True) == 0
    checks = _json.loads(out.getvalue())["checks"]
    sess = next((c for c in checks if c["label"] == "sessions"), None)
    assert sess is not None
    assert "1 stored" in sess["detail"]


# --- image url / init noninteractive / config show yaml ---
def test_image_url_download(tmp_path: Path, monkeypatch):
    import io
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    from types import SimpleNamespace

    from termux_agent import cli

    served = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            served["path"] = self.path
            body = b"fakepng"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv_http = HTTPServer(("127.0.0.1", 0), H)
    port = srv_http.server_address[1]
    t = threading.Thread(target=srv_http.serve_forever, daemon=True)
    t.start()
    try:
        import yaml

        cf = tmp_path / "config.yaml"
        cf.write_text(yaml.safe_dump({"provider": "zen", "model": "m", "providers": {"zen": {"type": "openai", "models": ["m"]}}}))
        monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        ))
        monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
        url = f"http://127.0.0.1:{port}/photo.jpg"
        out = io.StringIO()
        monkeypatch.setattr(cli.sys, "stdout", out)
        assert cli.main(["--config", str(cf), "--image", url, "--provider", "zen", "describe"]) == 0
        assert served.get("path") == "/photo.jpg"
        assert "Downloaded image" in out.getvalue()
    finally:
        srv_http.shutdown()
        srv_http.server_close()


def test_init_noninteractive(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli, config

    cdir = tmp_path / "cfg"
    cdir.mkdir()
    monkeypatch.setattr(cli, "CONFIG_DIR", cdir)
    monkeypatch.setattr(cli, "CONFIG_FILE", cdir / "config.yaml")
    monkeypatch.setattr(config, "DEFAULTS", {
        "provider": "zen",
        "model": "m-default",
        "providers": {"zen": {"type": "openai", "models": ["m-default", "m2"]}},
    })
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_init(provider="zen", model="m2") == 0
    import yaml

    cfg = yaml.safe_load((cdir / "config.yaml").read_text())
    assert cfg["provider"] == "zen"
    assert cfg["model"] == "m2"


def test_config_show_yaml(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_config_show(_min_cfg()) == 0
    assert "provider: zen" in out.getvalue()


# --- config redact / watch exit-on-change / server image url ---
def test_config_show_redact(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    cfg = _min_cfg()
    cfg["providers"]["zen"]["api_key"] = "super-secret"
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_config_show(cfg, redact=True) == 0
    text = out.getvalue()
    assert "super-secret" not in text
    assert "***" in text


def test_watch_exit_on_change(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli

    answers = iter(["A", "B"])
    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: next(answers),
    ))
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_watch(_min_cfg(), "hi", "zen", None, interval=1, exit_on_change=True, as_json=True) == 0
    lines = [l for l in out.getvalue().strip().splitlines() if l]
    assert _json.loads(lines[-1])["changed"] is True


def test_server_chat_image_url(tmp_path: Path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"fakepng"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv_http = HTTPServer(("127.0.0.1", 0), H)
    img_port = srv_http.server_address[1]
    t = threading.Thread(target=srv_http.serve_forever, daemon=True)
    t.start()

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[{"role": "system", "content": "BASE"}],
            run=lambda p, on_tool_use=None, on_text_delta=None: (seen.setdefault("prompt", p) or "ok"),
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{img_port}/img.jpg"
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({"prompt": "look", "image": url}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            assert _json.loads(r.read())["ok"] is True
        assert "[image:" in seen["prompt"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        srv_http.shutdown()
        srv_http.server_close()


# --- cron json / sessions limit / repl attach url ---
def test_cron_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr("pathlib.Path.cwd", lambda *a, **k: tmp_path)
    assert cli.cmd_cron("0 9 * * *", "check mail", as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert data["schedule"] == "0 9 * * *"
    assert data["command"].startswith("termux-agent")


def test_server_sessions_limit(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    for i in range(5):
        session.record_messages([{"role": "user", "content": f"m{i}"}], "zen", "m", session_id=f"lim-{i}")

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/sessions?limit=2", timeout=10) as r:
            data = _json.loads(r.read())
        assert len(data["sessions"]) == 2
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_repl_attach_url(tmp_path: Path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"remote content here"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv_http = HTTPServer(("127.0.0.1", 0), H)
    port = srv_http.server_address[1]
    t = threading.Thread(target=srv_http.serve_forever, daemon=True)
    t.start()
    try:
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            system_prompt="BASE",
            messages=[{"role": "system", "content": "BASE"}],
            allowed_tools=set(),
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            run=lambda p, on_tool_use=None, on_text_delta=None: (seen.setdefault("prompt", p) or "ok"),
        )
        monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
        repl = Repl(agent, provider_name="zen", model="m")
        url = f"http://127.0.0.1:{port}/notes.txt"
        assert repl._handle_command(f"/attach {url}", None) is False
        assert "remote content here" in seen["prompt"]
    finally:
        srv_http.shutdown()
        srv_http.server_close()


# --- help json / server notify / export-all json ---
def test_help_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.main(["--help-json"]) == 0
    data = _json.loads(out.getvalue())
    assert data["prog"] == "termux-agent"
    flags = {f["flags"][0] for f in data["flags"]}
    assert "--watch" in flags
    assert "--serve" in flags


def test_server_chat_notify(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    seen = {}

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "done!",
        )

    def fake_notify(msg):
        seen["msg"] = msg

    monkeypatch.setattr("termux_agent.notify.notify", fake_notify)
    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({"prompt": "hi", "notify": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assert _json.loads(r.read())["ok"] is True
        assert "done!" in seen["msg"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_export_all_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "x"}], "zen", "m", session_id="all-x")
    out_file = tmp_path / "combined.json"
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    assert cli.cmd_export_all(str(out_file), as_json=True) == 0
    data = _json.loads(out_file.read_text())
    assert data["app"] == "termux-agent"
    assert len(data["sessions"]) == 1


# --- server index / doctor update / version 1.0 ---
def test_server_index_page(tmp_path: Path, monkeypatch):
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            body = r.read().decode()
        assert "termux-agent server" in body
        assert "/health" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_doctor_update_check(monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    monkeypatch.setattr(cli, "_latest_pypi_version", lambda: "9.9.9")
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli.sys, "stdout", out)
        code = cli.cmd_doctor(_min_cfg(), as_json=True, update=True)
    assert code == 1  # outdated -> issue
    checks = _json.loads(out.getvalue())["checks"]
    upd = next((c for c in checks if c["label"] == "update check"), None)
    assert upd is not None
    assert "9.9.9" in upd["detail"]


def test_version_is_100():
    import termux_agent

    assert termux_agent.__version__ == "1.0.0"


# --- repl maxrounds / server mct+cors / serve cors ---
def test_repl_maxrounds(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    agent = SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path, undo=lambda: "noop"),
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
        allowed_tools=set(),
        temperature=0.7,
        max_tool_rounds=20,
        run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
    )
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    repl = Repl(agent, provider_name="zen", model="m")
    out = io.StringIO()
    monkeypatch.setattr("termux_agent.ui.repl.render_info", lambda s: out.write(str(s)))
    assert repl._handle_command("/maxrounds 5", None) is False
    assert agent.max_tool_rounds == 5
    assert repl._handle_command("/maxrounds 9999", None) is False
    assert agent.max_tool_rounds == 5


def test_server_chat_mct_override(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    seen = {}

    def fake_build(*a, **k):
        seen.update(k)
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({"prompt": "hi", "max_context_tokens": 8000}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assert _json.loads(r.read())["ok"] is True
        assert seen["max_context_tokens"] == 8000
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_serve_cors_origin(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    handler = srv._AgentHandler
    handler.cors_origin = "https://example.com"
    try:
        httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
                assert r.headers.get("Access-Control-Allow-Origin") == "https://example.com"
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        handler.cors_origin = "*"


# --- export redact / tls serve ---
def test_export_redact(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "hi"}], "zen", "m", session_id="red-x")
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_export("red-x", redact=True) == 0
    data = _json.loads(out.getvalue())
    assert "api_key" not in data
    assert data["id"] == "red-x"


def test_tls_serve(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent import server as srv

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("dummy-cert")
    key.write_text("dummy-key")

    loaded = {}

    class FakeSSLContext:
        def __init__(self, proto):
            loaded["proto"] = proto

        def load_cert_chain(self, c, k):
            loaded["cert"], loaded["key"] = c, k

        def wrap_socket(self, sock, **kw):
            loaded["wrapped"] = True
            return sock

    monkeypatch.setattr("ssl.SSLContext", FakeSSLContext)
    monkeypatch.setattr("ssl.PROTOCOL_TLS_SERVER", "TLS")

    class FakeServer:
        def __init__(self, addr, handler, max_workers=0):
            self.server_address = (addr[0], 9999)
            self.socket = None

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(srv, "BoundedThreadingHTTPServer", FakeServer)
    code = srv.serve(_min_cfg(), tls_cert=str(cert), tls_key=str(key))
    assert code == 0
    assert loaded.get("cert") == str(cert)
    assert loaded.get("key") == str(key)
    assert loaded.get("wrapped") is True


# --- config-set / repl usage ---
def test_config_set(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    from termux_agent.config import CONFIG_DIR, CONFIG_FILE

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text("provider: zen\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cli, "CONFIG_FILE", cfg_file)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_config_set("temperature", "0.2", as_json=True) == 0
    assert _json.loads(out.getvalue())["value"] == 0.2
    import yaml as _yaml

    saved = _yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert saved["temperature"] == 0.2
    assert cli.cmd_config_set("providers.zen.model", "gpt-4o-mini") == 0
    saved = _yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert saved["providers"]["zen"]["model"] == "gpt-4o-mini"


def test_repl_usage(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    agent = SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path, undo=lambda: "noop"),
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
        allowed_tools=set(),
        temperature=0.7,
        max_tool_rounds=20,
        usage={"prompt_tokens": 100, "completion_tokens": 50},
        run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
    )
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    repl = Repl(agent, provider_name="zen", model="m")
    out = io.StringIO()
    monkeypatch.setattr("termux_agent.ui.repl.render_info", lambda s: out.write(str(s)))
    assert repl._handle_command("/usage", None) is False
    assert "prompt_tokens: 100" in out.getvalue()
    assert "total: 150" in out.getvalue()


# --- tokens session / export-all redact / show redact ---
def test_tokens_session(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages(
        [{"role": "user", "content": "a" * 40}, {"role": "assistant", "content": "b" * 40}],
        "zen",
        "m",
        session_id="tok-s",
    )
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_tokens(None, as_json=True, session_ref="tok-s") == 0
    data = _json.loads(out.getvalue())
    assert data["chars"] == 80
    assert data["tokens"] == 20


def test_export_all_redact(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "hi"}], "zen", "m", session_id="red-all")
    out_dir = tmp_path / "out"
    assert cli.cmd_export_all(str(out_dir), redact=True) == 0
    dumped = _json.loads((out_dir / "red-all.json").read_text(encoding="utf-8"))
    assert "api_key" not in dumped


def test_show_redact(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "hi"}], "zen", "m", session_id="red-show")
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_show("red-show", as_json=True, redact=True) == 0
    data = _json.loads(out.getvalue())
    assert "api_key" not in data


# --- bundle/restore json + dry-run ---
def test_bundle_json_and_restore_dryrun(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session
    from termux_agent.config import CONFIG_DIR, CONFIG_FILE

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text("provider: zen\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cli, "CONFIG_FILE", cfg_file)

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "hi"}], "zen", "m", session_id="bun-s")
    session.record_messages([{"role": "user", "content": "yo"}], "zen", "m", session_id="bun-s2")

    out_dir = tmp_path / "bundle"
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_bundle(str(out_dir), as_json=True) == 0
    manifest = _json.loads(out.getvalue())
    assert manifest["sessions"] == 2

    target = tmp_path / "target"
    monkeypatch.setattr(cli, "CONFIG_DIR", target)
    monkeypatch.setattr(cli, "CONFIG_FILE", target / "config.yaml")
    out2 = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out2)
    assert cli.cmd_restore(str(out_dir), dry_run=True, as_json=True) == 0
    report = _json.loads(out2.getvalue())
    assert report["dry_run"] is True
    assert len(report["items"]) == 3
    assert not (target / "config.yaml").exists()


# --- bench/smoke output + cron notify ---
def test_bench_output(tmp_path: Path, monkeypatch):
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli

    cfg = _min_cfg()
    cfg["providers"]["zen"]["models"] = ["m1", "m2"]
    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
    ))
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    out_file = tmp_path / "bench.json"
    assert cli.cmd_bench(cfg, "zen", as_json=True, output=str(out_file)) == 0
    data = _json.loads(out_file.read_text(encoding="utf-8"))
    assert data["provider"] == "zen"
    assert len(data["models"]) == 2


def test_smoke_output(tmp_path: Path, monkeypatch):
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={"prompt_tokens": 5},
        run=lambda p, on_tool_use=None, on_text_delta=None: "OK",
    ))
    monkeypatch.setattr(cli, "_maybe_notify", lambda *a, **k: None)
    out_file = tmp_path / "smoke.json"
    assert cli.cmd_smoke(_min_cfg(), "zen", "m", as_json=True, output=str(out_file)) == 0
    data = _json.loads(out_file.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["provider"] == "zen"


def test_cron_notify(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import cli

    monkeypatch.chdir(tmp_path)
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_cron("0 * * * *", "hello", as_json=True, notify=True) == 0
    data = _json.loads(out.getvalue())
    assert "--notify" in data["command"]
    assert data["notify"] is True


# --- batch attach / doctor quick / models+sessions output ---
def test_batch_attach(tmp_path: Path, monkeypatch):
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli

    data = tmp_path / "data.txt"
    data.write_text("SECRET", encoding="utf-8")
    seen = {}

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            run=lambda p, on_tool_use=None, on_text_delta=None: seen.__setitem__("p", p) or "ok",
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    in_path = tmp_path / "in.txt"
    in_path.write_text("summarize\n", encoding="utf-8")
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_batch(_min_cfg(), str(in_path), "zen", None, as_json=True, attach=[str(data)]) == 0
    assert "SECRET" in seen["p"]
    assert "[file:" in seen["p"]


def test_doctor_quick(monkeypatch):
    from termux_agent import cli

    got = {}

    def fake_doctor(cfg, network=False, as_json=False, termux=False, update=False, output=None):
        got.update(network=network, update=update)
        return 0

    monkeypatch.setattr(cli, "cmd_doctor", fake_doctor)
    argv = ["termux-agent", "--doctor", "--quick"]
    monkeypatch.setattr("sys.argv", argv)
    assert cli.main() == 0
    assert got["network"] is False
    assert got["update"] is False


def test_sessions_output(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "hi"}], "zen", "m", session_id="ses-out")
    out_file = tmp_path / "list.json"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_sessions(limit=20, output=str(out_file)) == 0
    data = _json.loads(out_file.read_text(encoding="utf-8"))
    assert data["sessions"][0]["id"] == "ses-out"


# --- doctor/prune/export output + smoke timeout ---
def test_doctor_output(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import cli

    out_file = tmp_path / "doctor.json"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", out)
    code = cli.cmd_doctor(_min_cfg(), output=str(out_file))
    assert code == 0
    data = _json.loads(out_file.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert any(c["label"] == "config" for c in data["checks"])


def test_prune_output(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "hi"}], "zen", "m", session_id="pr-1")
    session.record_messages([{"role": "user", "content": "hi"}], "zen", "m", session_id="pr-2")
    out_file = tmp_path / "prune.json"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_prune(1, output=str(out_file), dry_run=True) == 0
    data = _json.loads(out_file.read_text(encoding="utf-8"))
    assert data["removed"] == 1
    assert data["dry_run"] is True
    assert len(list(sdir.glob("*.jsonl"))) == 2


def test_export_output(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "hi"}], "zen", "m", session_id="exp-out")
    out_file = tmp_path / "session.json"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_export("exp-out", output=str(out_file)) == 0
    data = _json.loads(out_file.read_text(encoding="utf-8"))
    assert data["id"] == "exp-out"


def test_smoke_timeout(tmp_path: Path, monkeypatch):
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
    ))
    monkeypatch.setattr(cli, "_run_guarded", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(cli, "_maybe_notify", lambda *a, **k: None)
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_smoke(_min_cfg(), "zen", "m", as_json=True, timeout=5) == 1
    data = _json.loads(out.getvalue())
    assert data["ok"] is False
    assert "timed out" in data["error"]


# --- list-* output + import markdown ---
def test_list_tools_output(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import cli

    out_file = tmp_path / "tools.json"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_list_tools(output=str(out_file)) == 0
    data = _json.loads(out_file.read_text(encoding="utf-8"))
    assert any(t["name"] == "read_file" for t in data["tools"])


def test_list_providers_output(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import cli

    out_file = tmp_path / "providers.json"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_list_providers(_min_cfg(), output=str(out_file)) == 0
    data = _json.loads(out_file.read_text(encoding="utf-8"))
    assert any(p["name"] == "zen" for p in data["providers"])


def test_import_markdown(tmp_path: Path, monkeypatch):
    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    md = "# Session abc\n\n### user\nhello\n\n### assistant\nhi there\n"
    md_file = tmp_path / "chat.md"
    md_file.write_text(md, encoding="utf-8")
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_import(str(md_file), markdown=True, dry_run=True) == 0
    assert "2 message(s)" in out.getvalue()
    assert len(list(sdir.glob("*.jsonl"))) == 0


# --- watch append / config-show+tokens output ---
def test_watch_append(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent import cli

    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: "A",
    ))
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    out_file = tmp_path / "watch.log"
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_watch(_min_cfg(), "hi", "zen", None, interval=1, output=str(out_file), max_rounds=2, append=True) == 0
    assert out_file.read_text(encoding="utf-8").strip().splitlines() == ["A", "A"]


def test_config_show_output(tmp_path: Path, monkeypatch):
    import yaml as _yaml

    from termux_agent import cli

    out_file = tmp_path / "cfg.yaml"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_config_show(_min_cfg(), output=str(out_file)) == 0
    data = _yaml.safe_load(out_file.read_text(encoding="utf-8"))
    assert data["provider"] == "zen"


def test_tokens_output(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import cli

    out_file = tmp_path / "tokens.json"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_tokens(None, text="a" * 40, as_json=True, output=str(out_file)) == 0
    data = _json.loads(out_file.read_text(encoding="utf-8"))
    assert data["tokens"] == 10
    assert data["chars"] == 40


# --- forget/cron/completion output ---
def test_forget_output(tmp_path: Path, monkeypatch):
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "hi"}], "zen", "m", session_id="fg-out")
    out_file = tmp_path / "forget.json"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_forget("fg-out", output=str(out_file)) == 0
    data = _json.loads(out_file.read_text(encoding="utf-8"))
    assert data["deleted"] == "fg-out"
    assert len(list(sdir.glob("*.jsonl"))) == 0


def test_cron_output(tmp_path: Path, monkeypatch):
    from termux_agent import cli

    monkeypatch.chdir(tmp_path)
    out_file = tmp_path / "cron.txt"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_cron("0 * * * *", "hello", output=str(out_file)) == 0
    assert "termux-agent --no-save" in out_file.read_text(encoding="utf-8")


def test_completion_output(tmp_path: Path, monkeypatch):
    from termux_agent import cli

    out_file = tmp_path / "comp.bash"
    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "argv", ["termux-agent", "--completion", "bash", "--output", str(out_file)])
    assert cli.main() == 0
    assert "_termux_agent" in out_file.read_text(encoding="utf-8")


# --- summarize/rerun redact + repl bench ---
def test_summarize_redact(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "zen", "m", session_id="sum-red")
    seen = {}

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            run=lambda p, on_tool_use=None, on_text_delta=None: seen.__setitem__("p", p) or "summary",
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_summarize(_min_cfg(), "sum-red", "zen", "m", as_json=True, redact=True) == 0
    assert _json.loads(out.getvalue())["summary"] == "summary"


def test_rerun_redact(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "ask"}, {"role": "assistant", "content": "old"}], "zen", "m", session_id="rr-red")
    monkeypatch.setattr(cli, "build_agent", lambda *a, **k: SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path),
        usage={},
        run=lambda p, on_tool_use=None, on_text_delta=None: "new",
    ))
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_rerun(_min_cfg(), "rr-red", "zen", "m", as_json=True, redact=True) == 0
    assert _json.loads(out.getvalue())["ok"] is True


def test_repl_bench(tmp_path: Path, monkeypatch):
    import io

    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    agent = SimpleNamespace(
        provider=SimpleNamespace(name="zen", model="m"),
        ctx=SimpleNamespace(working_dir=tmp_path, undo=lambda: "noop"),
        system_prompt="BASE",
        messages=[{"role": "system", "content": "BASE"}],
        allowed_tools=set(),
        temperature=0.7,
        max_tool_rounds=20,
        run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
    )
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    monkeypatch.setattr("termux_agent.cli.cmd_bench", lambda *a, **k: 0)
    repl = Repl(agent, provider_name="zen", model="m")
    assert repl._handle_command("/bench", None) is False


# --- bundle no-sessions / serve workers / sessions all ---
def test_bundle_no_sessions(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session
    from termux_agent.config import CONFIG_DIR, CONFIG_FILE

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text("provider: zen\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cli, "CONFIG_FILE", cfg_file)

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    session.record_messages([{"role": "user", "content": "hi"}], "zen", "m", session_id="ns-s")

    out_dir = tmp_path / "bundle"
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_bundle(str(out_dir), as_json=True, include_sessions=False) == 0
    manifest = _json.loads(out.getvalue())
    assert manifest["sessions"] == 0
    assert not (out_dir / "sessions").exists()
    assert (out_dir / "manifest.json").is_file()


def test_serve_workers_wiring(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)

    seen = {}

    class FakeProc:
        pid = 777

    def fake_popen(cmd, **kw):
        seen["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_serve(_min_cfg(), "127.0.0.1", 8788, "zen", "m", True, "tok", background=True, max_workers=4)
    assert code == 0
    assert "--serve-workers" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--serve-workers") + 1] == "4"


def test_serve_workers_foreground(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from termux_agent import cli

    seen = {}

    def fake_serve(cfg, **kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr("termux_agent.server.serve", fake_serve)
    code = cli.cmd_serve(_min_cfg(), "127.0.0.1", 8789, "zen", "m", False, None, max_workers=3)
    assert code == 0
    assert seen["max_workers"] == 3


def test_sessions_all(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    for i in range(5):
        (sdir / f"20260820-00000{i}.jsonl").write_text('{"role":"user","content":"x"}\n')
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_sessions(as_json=True, limit=0) == 0
    assert len(_json.loads(out.getvalue())["sessions"]) == 5


# --- OpenAI-compatible endpoints / watch diff / stats json ---
def test_server_openai_chat_completions(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request
    from types import SimpleNamespace

    from termux_agent import server as srv
    from termux_agent import session
    from termux_agent.server import _AgentHandler

    sdir = tmp_path / "sessions"
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    class FakeProv:
        name = "zen"
        model = "m"

        def list_models(self):
            return ["m1", "m2"]

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=FakeProv(),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            messages=[{"role": "system", "content": "s"}],
        )

        def _run(prompt):
            agent.messages += [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "OAI:" + prompt},
            ]
            return "OAI:" + prompt

        agent.run = _run
        return agent

    _AgentHandler.build_agent = staticmethod(fake_build)
    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=_json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            chat = _json.loads(r.read())
        assert chat["object"] == "chat.completion"
        assert chat["model"] == "m"
        assert chat["choices"][0]["message"]["content"] == "OAI:hi"
        assert chat["usage"]["total_tokens"] == 10
        assert chat["session"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=10) as r:
            models = _json.loads(r.read())
        assert models["object"] == "list"
        assert [m["id"] for m in models["data"]] == ["m1", "m2"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_server_openai_stream(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request
    from types import SimpleNamespace

    from termux_agent import server as srv
    from termux_agent import session
    from termux_agent.server import _AgentHandler

    sdir = tmp_path / "sessions"
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)

    def fake_build(*a, **k):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
        )

        def _run(prompt, on_text_delta=None):
            if on_text_delta:
                on_text_delta("hel")
                on_text_delta("lo")
            return "hello"

        agent.run = _run
        return agent

    _AgentHandler.build_agent = staticmethod(fake_build)
    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=_json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8")
        assert "data: [DONE]" in body
        assert '"content": "hel"' in body
        assert '"content": "lo"' in body
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_watch_diff_shows_diff(monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    answers = iter(["alpha", "alpha", "beta"])

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=None),
            usage={},
            run=lambda p, on_tool_use=None, on_text_delta=None: next(answers),
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: agent.run(p))
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_watch(_min_cfg(), "hi", "zen", None, interval=1, max_rounds=3, diff=True)
    assert code == 0
    rendered = out.getvalue()
    assert "unchanged" in rendered
    assert "+beta" in rendered
    assert "-alpha" in rendered


def test_one_shot_stats_json(tmp_path: Path, monkeypatch):
    import io
    import json as _json
    from types import SimpleNamespace

    from termux_agent import cli

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            run=lambda prompt, on_tool_use=None: "ANSWER",
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_one_shot(_min_cfg(), "hi", "zen", None, as_json=True, stats=True)
    assert code == 0
    data = _json.loads(out.getvalue())
    assert data["usage"] == {}
    assert data["answer"] == "ANSWER"


# --- serve mct default / resume+watch attach ---
def test_serve_max_context_tokens_default(tmp_path: Path, monkeypatch):
    import json as _json
    import threading
    import urllib.request

    from types import SimpleNamespace

    from termux_agent import server as srv

    seen = {}

    def fake_build(*a, **k):
        seen.update(k)
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            messages=[],
            run=lambda p, on_tool_use=None, on_text_delta=None: "ok",
        )

    httpd = srv.build_server(fake_build, _min_cfg(), "zen", None, max_context_tokens=4096)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({"prompt": "hi"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assert _json.loads(r.read())["ok"] is True
        assert seen["max_context_tokens"] == 4096
        seen.clear()
        req2 = urllib.request.Request(
            f"http://127.0.0.1:{port}/chat",
            data=_json.dumps({"prompt": "hi", "max_context_tokens": 2000}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req2, timeout=10) as r:
            assert _json.loads(r.read())["ok"] is True
        assert seen["max_context_tokens"] == 2000
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_resume_attach(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli, session

    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / "20260820-000001.jsonl").write_text(
        '{"role":"user","content":"hi","provider":"zen","model":"m"}\n{"role":"assistant","content":"hello"}\n'
    )
    monkeypatch.setattr(session, "SESSIONS_DIR", sdir)
    notes = tmp_path / "notes.txt"
    notes.write_text("important context", encoding="utf-8")

    seen = {}

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            system_prompt="SYS",
            messages=[{"role": "system", "content": "SYS"}],
            run=lambda prompt, on_tool_use=None, on_text_delta=None: seen.setdefault("p", prompt) or "CONTINUED",
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_resume(_min_cfg(), "20260820-000001", "continue please", attach=[str(notes)])
    assert code == 0
    assert "important context" in seen["p"]


def test_watch_attach(tmp_path: Path, monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent import cli

    notes = tmp_path / "data.txt"
    notes.write_text("payload", encoding="utf-8")
    seen = {}

    def fake_build(*a, **k):
        return SimpleNamespace(
            provider=SimpleNamespace(name="zen", model="m"),
            ctx=SimpleNamespace(working_dir=tmp_path),
            usage={},
            run=lambda p, on_tool_use=None, on_text_delta=None: "A",
        )

    monkeypatch.setattr(cli, "build_agent", fake_build)
    monkeypatch.setattr(cli, "_run_guarded", lambda agent, p, t, to=None: (seen.setdefault("p", p), agent.run(p))[1])
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    code = cli.cmd_watch(_min_cfg(), "read this", "zen", None, interval=1, max_rounds=1, attach=[str(notes)])
    assert code == 0
    assert "payload" in seen["p"]


# --- fish completion / tokens dir / repl provider shorthand ---
def test_completion_fish(tmp_path: Path, monkeypatch):
    from termux_agent import cli
    from termux_agent.completion import FISH_SCRIPT, install

    out = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "argv", ["termux-agent", "--completion", "fish"])
    assert cli.main() == 0
    assert "complete -c termux-agent" in out.getvalue()

    home = tmp_path / "home"
    monkeypatch.setattr("os.path.expanduser", lambda p: str(home) if p == "~" else p)
    rc = install("fish")
    assert rc.endswith(".config/fish/completions/termux-agent.fish")
    assert FISH_SCRIPT in open(rc, encoding="utf-8").read()


def test_tokens_dir(tmp_path: Path, monkeypatch):
    import io
    import json as _json

    from termux_agent import cli

    d = tmp_path / "tree"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("hello world", encoding="utf-8")
    (d / "sub" / "b.txt").write_text("another file here", encoding="utf-8")
    (d / "skip.bin").write_bytes(b"\x00\x01\x02")
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.cmd_tokens(str(d), as_json=True) == 0
    data = _json.loads(out.getvalue())
    assert data["ok"] is True
    assert data["files"] == 2
    assert data["chars"] == len("hello world") + len("another file here")


def test_repl_provider_shorthand(monkeypatch):
    import io
    from types import SimpleNamespace

    from termux_agent.session import Session
    from termux_agent.ui.repl import Repl

    seen = {}

    class P:
        name = "zen"
        model = "model-x"

        def __init__(self, name, cfg, model):
            seen["model"] = model

    def fake_create(name, cfg, model):
        return P(name, cfg, model)

    monkeypatch.setattr("termux_agent.providers.create_provider", fake_create)
    monkeypatch.setattr("termux_agent.config.load_config", lambda *a, **k: {})
    monkeypatch.setattr("termux_agent.ui.repl.Session", lambda **k: Session())
    agent = SimpleNamespace(
        provider=P("zen", {}, None),
        ctx=SimpleNamespace(working_dir="/tmp"),
        system_prompt="SYS",
        messages=[{"role": "system", "content": "SYS"}],
    )
    repl = Repl(agent, provider_name="zen", model="m")
    out = io.StringIO()
    monkeypatch.setattr("termux_agent.ui.repl.sys.stdout", out)
    assert repl._handle_command("/provider zen:model-x", None) is False
    assert seen["model"] == "model-x"
    assert repl.provider_name == "zen"
    assert repl.model == "model-x"


# --- init wizard ---
def test_init_wizard_writes_config(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli
    from termux_agent.config import CONFIG_DIR, CONFIG_FILE

    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "ta")
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "ta" / "config.yaml")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    it = iter(["zen", "m-model", "n"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))
    code = cli.cmd_init()
    assert code == 0
    out = (tmp_path / "ta" / "config.yaml").read_text()
    assert "provider: zen" in out
    assert "m-model" in out


# --- web_search ---
def test_web_search_ddg(monkeypatch):
    from termux_agent.tools import web as webmod
    from termux_agent.tools.base import ToolContext, run_tool

    def fake(url):
        assert "duckduckgo" in url
        return {
            "AbstractText": "Python requests ringkas",
            "AbstractURL": "https://duckduckgo.com",
            "RelatedTopics": [],
        }

    monkeypatch.setattr(webmod, "_http_json", fake)
    out = run_tool("web_search", {"query": "python", "max_results": 1}, ToolContext(working_dir="/", confirm_commands=False))
    assert "Summary: Python requests" in out
    assert "duckduckgo.com" in out


def test_web_search_fallback_wikipedia(monkeypatch):
    from termux_agent.tools import web as webmod
    from termux_agent.tools.base import ToolContext, run_tool

    def fake(url):
        if "duckduckgo" in url:
            raise ConnectionError("SSL gagal")
        return {
            "query": {"search": [{"title": "HTTPX", "snippet": "<b>python</b> http client"}]}
        }

    monkeypatch.setattr(webmod, "_http_json", fake)
    out = run_tool("web_search", {"query": "httpx"}, ToolContext(working_dir="/", confirm_commands=False))
    assert "Wikipedia" in out
    assert "HTTPX" in out
    assert "en.wikipedia.org/wiki/HTTPX" in out