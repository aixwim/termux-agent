"""Test fitur tambahan: rules file, tool git, resume sesi."""
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
    assert "Dibatalkan" in r


def test_git_commit_confirmed(git_repo: Path):
    (git_repo / "a.txt").write_text("v2\n")
    ctx = ToolContext(working_dir=git_repo, confirm_commands=True, confirm=lambda _: True)
    r = run_tool("git_commit", {"message": "perbaikan"}, ctx)
    assert "perbaikan" in r
    log = subprocess.run(["git", "log", "--oneline"], cwd=git_repo, capture_output=True, text=True).stdout
    assert "perbaikan" in log


def test_git_commit_no_changes(git_repo: Path):
    ctx = ToolContext(working_dir=git_repo, confirm_commands=False)
    assert "Tidak ada perubahan" in run_tool("git_commit", {"message": "x"}, ctx)


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