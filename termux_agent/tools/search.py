"""Tool pencarian: grep teks dan glob nama file."""
from __future__ import annotations

import re
from pathlib import Path

from termux_agent.tools.base import ToolContext, tool


@tool(
    "grep_file",
    "Cari pola regex di dalam file/direktori. Hasil berformat path:nomor:baris.",
    {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Pola regex yang dicari"},
            "path": {"type": "string", "description": "File atau direktori yang dicari (default working_dir)"},
            "include": {"type": "string", "description": "Filter ekstensi/nama file (mis. '*.py', opsional)"},
            "max_results": {"type": "integer", "description": "Batas jumlah hasil (default 100)"},
        },
        "required": ["pattern"],
    },
)
def grep_file(args: dict, ctx: ToolContext) -> str:
    pattern = str(args["pattern"])
    raw = str(args.get("path") or ".")
    path = ctx.require_allowed(ctx.resolve(raw))
    include = str(args.get("include") or "")
    max_results = int(args.get("max_results", 100))
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"Error: regex tidak valid: {e}"
    targets: list[Path] = []
    if path.is_file():
        targets.append(path)
    elif path.is_dir():
        targets = list(path.rglob("*"))
    else:
        return f"Error: path tidak ditemukan: {path}"
    results: list[str] = []
    for t in targets:
        if t.is_dir():
            continue
        if include and not t.match(include) and not t.name.endswith(include):
            continue
        try:
            for i, line in enumerate(
                t.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                if rx.search(line):
                    results.append(f"{t}:{i}: {line}")
                    if len(results) >= max_results:
                        break
        except OSError:
            continue
        if len(results) >= max_results:
            break
    if not results:
        return "Tidak ada hasil."
    return "\n".join(results[:max_results])


@tool(
    "glob_find",
    "Cari file/direktori berdasarkan pola glob (mis. '**/*.py').",
    {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Pola glob, relatif ke working_dir"},
            "max_results": {"type": "integer", "description": "Batas jumlah hasil (default 200)"},
        },
        "required": ["pattern"],
    },
)
def glob_find(args: dict, ctx: ToolContext) -> str:
    pattern = str(args.get("pattern", "**/*"))
    max_results = int(args.get("max_results", 200))
    base = ctx.working_dir
    try:
        matches = [p for p in base.glob(pattern) if ctx.is_allowed(p)][:max_results]
    except (ValueError, OSError) as e:
        return f"Error: {e}"
    if not matches:
        return "Tidak ada hasil."
    lines = [f"{p.relative_to(base) if p.is_relative_to(base) else p}" for p in matches]
    return "\n".join(lines)