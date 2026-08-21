"""Shell tool: run commands in Termux via subprocess."""
from __future__ import annotations

import shlex
import subprocess
import os
import signal

from termux_agent.tools.base import ToolContext, tool

# Read-only commands safe to run without confirmation.
SAFE_COMMANDS = {
    "ls", "pwd", "cat", "echo", "head", "tail", "wc", "whoami", "uname",
    "date", "type", "which", "grep", "rg", "du", "df", "free", "uptime",
    "lsusb", "getprop", "termux-clipboard-get", "termux-battery-status",
    "termux-wifi-scaninfo",
}

SAFE_GIT_SUBCOMMANDS = {
    "diff",
    "log",
    "rev-parse",
    "show",
    "status",
}


def _has_shell_control(command: str) -> bool:
    """Return whether a command can compose or redirect shell operations."""
    return any(token in command for token in (";", "&&", "||", "|", ">", "<", "`", "$(", "\n", "\r"))


def _is_read_only_command(tokens: list[str]) -> bool:
    """Recognize commands whose arguments cannot directly modify local state."""
    if not tokens:
        return False
    base = tokens[0]
    if base in SAFE_COMMANDS:
        return True
    if base == "git":
        return len(tokens) > 1 and tokens[1] in SAFE_GIT_SUBCOMMANDS
    if base == "find":
        mutating = {"-delete", "-exec", "-execdir", "-ok", "-okdir"}
        return not any(token in mutating for token in tokens[1:])
    if base == "termux-volume":
        return len(tokens) == 1
    return False


@tool(
    "run_command",
    "Run a shell command in Termux. Runs in working_dir. "
    "Commands outside the safe list ask for confirmation. Output is truncated when too large.",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command (single line)"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: config)"},
        },
        "required": ["command"],
    },
)
def run_command(args: dict, ctx: ToolContext) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        return "Error: empty command"
    tokens = shlex.split(command)
    base_cmd = tokens[0]
    explicitly_allowed = any(
        command == prefix or command.startswith(prefix + " ")
        for prefix in ctx.whitelisted_commands
        if prefix
    )
    whitelisted = not _has_shell_control(command) and (
        _is_read_only_command(tokens) or explicitly_allowed
    )
    needs_confirm = not whitelisted
    if needs_confirm and ctx.confirm_commands:
        if ctx.confirm is None:
            return (
                f"Error: command '{base_cmd}' is not in the whitelist and confirmation is disabled. "
                "Run again in interactive mode or add it to whitelisted_commands."
            )
        ok = ctx.confirm(command)
        if not ok:
            return "Cancelled by user."
    timeout = int(args.get("timeout", ctx.command_timeout))
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(ctx.working_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, UnboundLocalError):
            try:
                proc.kill()
            except (OSError, UnboundLocalError):
                pass
        try:
            proc.communicate()
        except (OSError, ValueError):
            pass
        return f"Error: command exceeded timeout of {timeout}s"
    except OSError as e:
        return f"Error: failed to run: {e}"
    out = ""
    if stdout:
        out += stdout
    if stderr:
        out += "\n[stderr]\n" + stderr
    out = out.strip()
    if not out:
        out = f"(done, exit {proc.returncode}, no output)"
    prefix = f"$ {command}\nexit {proc.returncode}\n"
    return prefix + out
