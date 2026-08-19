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
        "--install-completion --list-providers --list-agents --image --prompt-file --api-key --search"
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