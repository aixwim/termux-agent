"""Shell tool: run commands in Termux via subprocess."""
from __future__ import annotations

import shlex
import subprocess

from termux_agent.tools.base import ToolContext, tool

# Read-only commands safe to run without confirmation.
SAFE_COMMANDS = {
    "ls", "pwd", "cat", "echo", "head", "tail", "wc", "whoami", "uname",
    "date", "env", "type", "which", "find", "grep", "rg", "du", "df", "free",
    "uptime", "lsusb", "getprop", "curl", "wget", "python3", "python", "git",
    "node", "npm", "cargo", "go", "termux-clipboard-get", "termux-battery-status",
    "termux-wifi-scaninfo", "termux-brightness", "termux-volume",
}


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
    base_cmd = shlex.split(command)[0]
    needs_confirm = base_cmd not in SAFE_COMMANDS
    if needs_confirm and ctx.confirm_commands:
        if ctx.confirm is None:
            return (
                f"Error: command '{base_cmd}' is not in the whitelist and confirmation is disabled. "
                "Run again in interactive mode or add it to SAFE_COMMANDS."
            )
        ok = ctx.confirm(command)
        if not ok:
            return "Cancelled by user."
    timeout = int(args.get("timeout", ctx.command_timeout))
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(ctx.working_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command exceeded timeout of {timeout}s"
    except OSError as e:
        return f"Error: failed to run: {e}"
    out = ""
    if proc.stdout:
        out += proc.stdout
    if proc.stderr:
        out += "\n[stderr]\n" + proc.stderr
    out = out.strip()
    if not out:
        out = f"(done, exit {proc.returncode}, no output)"
    prefix = f"$ {command}\nexit {proc.returncode}\n"
    return prefix + out