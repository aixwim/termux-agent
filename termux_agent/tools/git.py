"""Tool git: status, diff, dan commit (dengan konfirmasi)."""
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
        return -1, "", "perintah git melebihi batas waktu"
    except OSError as e:
        return -1, "", f"gagal menjalankan git: {e}"


def _git_output(rc: int, out: str, err: str) -> str:
    text = (out + ("\n[stderr]\n" + err if err else "")).strip()
    return text or f"(selesai, exit {rc}, tanpa output)"


@tool(
    "git_status",
    "Lihat status repo git di working_dir (file berubah/baru/terhapus).",
    {
        "type": "object",
        "properties": {
            "short": {"type": "boolean", "description": "Format singkat (default true)"},
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
    "Lihat diff perubahan yang belum di-commit di repo git.",
    {
        "type": "object",
        "properties": {
            "stat": {"type": "boolean", "description": "Hanya statistik perubahan (default true)"},
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
    "Commit semua perubahan di working_dir dengan pesan tertentu. Perlu konfirmasi pengguna.",
    {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Pesan commit"},
        },
        "required": ["message"],
    },
)
def git_commit(args: dict, ctx: ToolContext) -> str:
    message = str(args.get("message", "")).strip()
    if not message:
        return "Error: message commit kosong"
    rc, out, err = _git(ctx, "status", "--short")
    if rc != 0:
        return "Error: bukan repo git atau git bermasalah: " + (err or out).strip()
    if not (out.strip()):
        return "Tidak ada perubahan untuk di-commit."
    confirm = f"git add -A && git commit -m \"{message}\""
    if ctx.confirm_commands:
        if ctx.confirm is None:
            return "Error: commit butuh konfirmasi interaktif. Jalankan 'termux-agent' (mode interaktif) untuk izinkan commit."
        if not ctx.confirm(confirm):
            return "Dibatalkan oleh pengguna."
    rc1, _, err1 = _git(ctx, "add", "-A")
    if rc1 != 0:
        return f"Error git add: {err1}"
    rc2, out2, err2 = _git(ctx, "commit", "-m", message)
    if rc2 != 0:
        return f"Error git commit: {err2 or out2}"
    return out2.strip() or f"OK: commit dibuat ({message})"