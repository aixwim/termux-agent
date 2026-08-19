"""Tool file: baca, tulis, edit, dan daftar isi direktori."""
from __future__ import annotations

from pathlib import Path

from termux_agent.tools.base import ToolContext, tool


@tool(
    "read_file",
    "Baca isi file teks. Path relatif terhadap working_dir. Gunakan start_line/end_line untuk baca sebagian.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path file (relatif atau absolut)"},
            "start_line": {"type": "integer", "description": "Baris awal (1-based, opsional)"},
            "end_line": {"type": "integer", "description": "Baris akhir (opsional)"},
        },
        "required": ["path"],
    },
)
def read_file(args: dict, ctx: ToolContext) -> str:
    path = ctx.require_allowed(ctx.resolve(str(args["path"])))
    if not path.is_file():
        return f"Error: file tidak ditemukan: {path}"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return f"Error: tidak bisa baca file: {e}"
    start = int(args.get("start_line", 1))
    end = int(args.get("end_line", len(lines)))
    start = max(1, start)
    end = min(len(lines), max(start, end))
    body = "\n".join(f"{i}: {lines[i-1]}" for i in range(start, end + 1))
    return f"{path} ({len(lines)} baris total)\n{body}"


@tool(
    "write_file",
    "Tulis/menimpa file dengan konten penuh. Membuat direktori induk bila perlu.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path file tujuan"},
            "content": {"type": "string", "description": "Konten penuh file"},
        },
        "required": ["path", "content"],
    },
)
def write_file(args: dict, ctx: ToolContext) -> str:
    path = ctx.require_allowed(ctx.resolve(str(args["path"])))
    content = str(args.get("content", ""))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Error: tidak bisa menulis file: {e}"
    return f"OK: menulis {len(content)} karakter ke {path}"


@tool(
    "edit_file",
    "Edit file dengan mengganti teks lama dengan teks baru (string replacement tunggal).",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path file"},
            "old_string": {"type": "string", "description": "Teks persis yang akan diganti"},
            "new_string": {"type": "string", "description": "Teks pengganti"},
        },
        "required": ["path", "old_string", "new_string"],
    },
)
def edit_file(args: dict, ctx: ToolContext) -> str:
    path = ctx.require_allowed(ctx.resolve(str(args["path"])))
    old = str(args.get("old_string", ""))
    new = str(args.get("new_string", ""))
    if not old:
        return "Error: old_string tidak boleh kosong"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error: tidak bisa baca file: {e}"
    count = content.count(old)
    if count == 0:
        return f"Error: old_string tidak ditemukan di {path}"
    if count > 1:
        return f"Error: old_string ditemukan {count} kali; buat old_string lebih unik."
    content = content.replace(old, new, 1)
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Error: tidak bisa menulis file: {e}"
    return f"OK: 1 penggantian di {path}"


@tool(
    "list_dir",
    "Daftar isi direktori (nama, ukuran, jenis). Tanpa argumen memakai working_dir.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Direktori yang didaftar (opsional)"},
            "pattern": {"type": "string", "description": "Filter nama file (opsional)"},
        },
        "required": [],
    },
)
def list_dir(args: dict, ctx: ToolContext) -> str:
    raw = str(args.get("path") or ".")
    path = ctx.require_allowed(ctx.resolve(raw))
    if not path.is_dir():
        return f"Error: bukan direktori: {path}"
    pattern = str(args.get("pattern") or "")
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    lines = []
    for e in entries:
        if pattern and pattern not in e.name:
            continue
        kind = "DIR" if e.is_dir() else "file"
        size = "" if e.is_dir() else f"{e.stat().st_size}B"
        lines.append(f"{kind:4} {e.name:40} {size}")
    return f"{path} ({len(lines)} entri)\n" + "\n".join(lines)