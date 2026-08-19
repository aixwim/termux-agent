"""Tool shell: jalankan perintah di Termux via subprocess."""
from __future__ import annotations

import shlex
import subprocess

from termux_agent.tools.base import ToolContext, tool

# Perintah baca-saja yang aman dijalankan tanpa konfirmasi.
SAFE_COMMANDS = {
    "ls", "pwd", "cat", "echo", "head", "tail", "wc", "whoami", "uname",
    "date", "env", "type", "which", "find", "grep", "rg", "du", "df", "free",
    "uptime", "lsusb", "getprop", "curl", "wget", "python3", "python", "git",
    "node", "npm", "cargo", "go", "termux-clipboard-get", "termux-battery-status",
    "termux-wifi-scaninfo", "termux-brightness", "termux-volume",
}


@tool(
    "run_command",
    "Jalankan perintah shell di Termux. Berjalan di working_dir. "
    "Perintah selain daftar aman akan minta konfirmasi. Hasil dipotong bila terlalu besar.",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Perintah shell (satu baris)"},
            "timeout": {"type": "integer", "description": "Timeout detik (default: config)"},
        },
        "required": ["command"],
    },
)
def run_command(args: dict, ctx: ToolContext) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        return "Error: command kosong"
    base_cmd = shlex.split(command)[0]
    needs_confirm = base_cmd not in SAFE_COMMANDS
    if needs_confirm and ctx.confirm_commands:
        if ctx.confirm is None:
            return (
                f"Error: perintah '{base_cmd}' tidak ada di whitelist dan konfirmasi nonaktif. "
                "Jalankan ulang dalam mode interaktif atau tambahkan ke SAFE_COMMANDS."
            )
        ok = ctx.confirm(command)
        if not ok:
            return "Dibatalkan oleh pengguna."
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
        return f"Error: perintah melebihi batas waktu {timeout}s"
    except OSError as e:
        return f"Error: gagal menjalankan: {e}"
    out = ""
    if proc.stdout:
        out += proc.stdout
    if proc.stderr:
        out += "\n[stderr]\n" + proc.stderr
    out = out.strip()
    if not out:
        out = f"(selesai, exit {proc.returncode}, tanpa output)"
    prefix = f"$ {command}\nexit {proc.returncode}\n"
    return prefix + out