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


# --- init wizard ---
def test_init_wizard_writes_config(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli
    from termux_agent.config import CONFIG_DIR, CONFIG_FILE

    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "ta")
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "ta" / "config.yaml")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    it = iter(["zen", "m-model"])
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