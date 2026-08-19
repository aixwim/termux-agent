"""Git tools: status, diff, and commit (with confirmation)."""
from __future__ import annotations

import subprocess

from termux_agent.tools.base import ToolContext, tool


def _git(ctx: ToolContext, *args: str, timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(ctx.working_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "git command exceeded the timeout"
    except OSError as e:
        return -1, "", f"failed to run git: {e}"


def _git_output(rc: int, out: str, err: str) -> str:
    text = (out + ("\n[stderr]\n" + err if err else "")).strip()
    return text or f"(done, exit {rc}, no output)"


@tool(
    "git_status",
    "Show git repo status in working_dir (modified/new/deleted files).",
    {
        "type": "object",
        "properties": {
            "short": {"type": "boolean", "description": "Short format (default true)"},
        },
        "required": [],
    },
)
def git_status(args: dict, ctx: ToolContext) -> str:
    if bool(args.get("short", True)):
        rc, out, err = _git(ctx, "status", "--short")
    else:
        rc, out, err = _git(ctx, "status")
    return _git_output(rc, out, err)


@tool(
    "git_diff",
    "Show uncommitted changes diff in the git repo.",
    {
        "type": "object",
        "properties": {
            "stat": {"type": "boolean", "description": "Show only change statistics (default true)"},
        },
        "required": [],
    },
)
def git_diff(args: dict, ctx: ToolContext) -> str:
    if bool(args.get("stat", True)):
        rc, out, err = _git(ctx, "diff", "--stat")
    else:
        rc, out, err = _git(ctx, "diff")
    return _git_output(rc, out, err)


@tool(
    "git_commit",
    "Commit all changes in working_dir with a given message. Requires user confirmation.",
    {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Commit message"},
        },
        "required": ["message"],
    },
)
def git_commit(args: dict, ctx: ToolContext) -> str:
    message = str(args.get("message", "")).strip()
    if not message:
        return "Error: empty commit message"
    rc, out, err = _git(ctx, "status", "--short")
    if rc != 0:
        return "Error: not a git repo or git has an issue: " + (err or out).strip()
    if not (out.strip()):
        return "No changes to commit."
    confirm = f"git add -A && git commit -m \"{message}\""
    if ctx.confirm_commands:
        if ctx.confirm is None:
            return "Error: commit needs interactive confirmation. Run 'termux-agent' (interactive mode) to allow commits."
        if not ctx.confirm(confirm):
            return "Cancelled by user."
    rc1, _, err1 = _git(ctx, "add", "-A")
    if rc1 != 0:
        return f"Error git add: {err1}"
    rc2, out2, err2 = _git(ctx, "commit", "-m", message)
    if rc2 != 0:
        return f"Error git commit: {err2 or out2}"
    return out2.strip() or f"OK: commit created ({message})"