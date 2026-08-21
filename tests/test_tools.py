"""Test tool handler: baca/tulis/edit, pencarian, shell, batas direktori."""
import subprocess
from pathlib import Path

import pytest

from termux_agent.tools import files, git, search, shell  # noqa: F401  # register tools
from termux_agent.tools.base import ToolContext, run_tool, tool_specs


@pytest.fixture
def tmp_work(tmp_path: Path) -> Path:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sample.txt").write_text("ini isi sample\n", encoding="utf-8")
    (tmp_path / "sub" / "app.py").write_text("def main():\n    print('x')\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def ctx(tmp_work: Path) -> ToolContext:
    return ToolContext(working_dir=tmp_work, confirm_commands=False)


def test_all_tools_registered():
    specs = {s.name for s in tool_specs()}
    assert specs == {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "list_tree",
        "grep_file",
        "glob_find",
        "run_command",
        "web_fetch",
            "web_search",
        "git_status",
        "git_diff",
        "git_log",
        "git_commit",
    }


def test_write_read_edit_roundtrip(ctx: ToolContext, tmp_work: Path):
    assert "OK" in run_tool("write_file", {"path": "hello.py", "content": "a=1\nb=2\n"}, ctx)
    r = run_tool("read_file", {"path": "hello.py"}, ctx)
    assert "a=1" in r and "b=2" in r
    assert "OK" in run_tool("edit_file", {"path": "hello.py", "old_string": "b=2", "new_string": "c=3"}, ctx)
    r = run_tool("read_file", {"path": "hello.py"}, ctx)
    assert "c=3" in r and "b=2" not in r


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("write_file", {"path": "sample.txt", "content": "replacement"}),
        (
            "edit_file",
            {
                "path": "sample.txt",
                "old_string": "ini isi sample",
                "new_string": "replacement",
            },
        ),
    ],
)
def test_file_mutations_preserve_target_when_atomic_write_fails(
    ctx: ToolContext,
    tmp_work: Path,
    monkeypatch,
    tool_name,
    arguments,
):
    from termux_agent import storage

    original = (tmp_work / "sample.txt").read_text(encoding="utf-8")

    def fail_write(*args, **kwargs):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(storage, "atomic_write_text", fail_write)
    result = run_tool(tool_name, arguments, ctx)

    assert "simulated storage failure" in result
    assert (tmp_work / "sample.txt").read_text(encoding="utf-8") == original
    assert ctx.undo_stack == []


def test_failed_undo_remains_retryable(ctx: ToolContext, tmp_work: Path, monkeypatch):
    from termux_agent import storage

    assert "OK" in run_tool(
        "write_file",
        {"path": "sample.txt", "content": "changed"},
        ctx,
    )

    def fail_write(*args, **kwargs):
        raise OSError("simulated undo failure")

    monkeypatch.setattr(storage, "atomic_write_text", fail_write)
    assert "simulated undo failure" in ctx.undo()
    assert len(ctx.undo_stack) == 1
    assert (tmp_work / "sample.txt").read_text(encoding="utf-8") == "changed"


def test_edit_non_unique_rejected(ctx: ToolContext, tmp_work: Path):
    (tmp_work / "dup.txt").write_text("x\nx\n")
    r = run_tool("edit_file", {"path": "dup.txt", "old_string": "x", "new_string": "y"}, ctx)
    assert "make old_string more unique" in r


def test_path_escape_blocked(ctx: ToolContext):
    outside = Path("/tmp/escape_test.txt")
    outside.write_text("secret")
    r = run_tool("read_file", {"path": str(outside)}, ctx)
    assert "Access denied" in r
    outside.unlink(missing_ok=True)


def test_missing_file_reports_error(ctx: ToolContext):
    assert "not found" in run_tool("read_file", {"path": "nope.txt"}, ctx)


def test_grep_and_glob(ctx: ToolContext):
    assert "app.py" in run_tool("grep_file", {"pattern": "def main"}, ctx)
    assert "sub/app.py" in run_tool("glob_find", {"pattern": "**/*.py"}, ctx)


def test_list_dir(ctx: ToolContext):
    r = run_tool("list_dir", {"path": "sub"}, ctx)
    assert "app.py" in r


def test_run_command_safe_no_confirm(ctx: ToolContext):
    r = run_tool("run_command", {"command": "echo halo"}, ctx)
    assert "exit 0" in r and "halo" in r


def test_safe_command_with_shell_control_needs_confirmation(tmp_work: Path):
    destination = tmp_work / "altered"
    context = ToolContext(
        working_dir=tmp_work,
        confirm_commands=True,
        confirm=None,
    )
    result = run_tool(
        "run_command",
        {"command": "echo unsafe > altered"},
        context,
    )
    assert "not in the whitelist" in result
    assert not destination.exists()


@pytest.mark.parametrize(
    "command",
    [
        "git clean -fdx",
        "find . -delete",
        "python -c pass",
        "curl -o download https://example.com",
        "termux-volume music 0",
    ],
)
def test_mutating_safe_named_commands_need_confirmation(tmp_work: Path, command):
    context = ToolContext(
        working_dir=tmp_work,
        confirm_commands=True,
        confirm=None,
    )

    result = run_tool("run_command", {"command": command}, context)

    assert "not in the whitelist" in result


@pytest.mark.parametrize(
    "command",
    ["find . -name '*.txt'", "git status", "git diff", "termux-volume"],
)
def test_argument_checked_read_only_commands_skip_confirmation(
    tmp_work: Path, command
):
    context = ToolContext(
        working_dir=tmp_work,
        confirm_commands=True,
        confirm=None,
    )

    result = run_tool("run_command", {"command": command}, context)

    assert "not in the whitelist" not in result


def test_run_command_non_whitelist_confirmed(tmp_work: Path):
    c = ToolContext(working_dir=tmp_work, confirm_commands=True, confirm=lambda _: True)
    assert "exit 0" in run_tool("run_command", {"command": "touch x"}, c)


def test_run_command_refused(tmp_work: Path):
    c = ToolContext(working_dir=tmp_work, confirm_commands=True, confirm=lambda _: False)
    assert "Cancelled by user." in run_tool("run_command", {"command": "touch y"}, c)


def test_run_command_failure(ctx: ToolContext):
    assert "exit 1" in run_tool("run_command", {"command": "false"}, ctx)


def test_run_command_timeout(tmp_work: Path):
    c = ToolContext(working_dir=tmp_work, confirm_commands=False, command_timeout=1)
    assert "exceeded timeout" in run_tool("run_command", {"command": "sleep 5"}, c)


def test_run_command_cwd(ctx: ToolContext):
    assert "sample.txt" in run_tool("run_command", {"command": "ls"}, ctx)


def test_output_truncation(tmp_work: Path):
    c = ToolContext(working_dir=tmp_work, confirm_commands=False, max_output_chars=50)
    r = run_tool("run_command", {"command": "seq 1 100"}, c)
    assert "output truncated" in r and len(r) <= 200
