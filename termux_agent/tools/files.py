"""File tools: read, write, edit, and list directory contents."""
from __future__ import annotations

from pathlib import Path

from termux_agent.tools.base import ToolContext, tool


@tool(
    "read_file",
    "Read a text file. Path is relative to working_dir. Use start_line/end_line to read part of it.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (relative or absolute)"},
            "start_line": {"type": "integer", "description": "Start line (1-based, optional)"},
            "end_line": {"type": "integer", "description": "End line (optional)"},
        },
        "required": ["path"],
    },
)
def read_file(args: dict, ctx: ToolContext) -> str:
    path = ctx.require_allowed(ctx.resolve(str(args["path"])))
    if not path.is_file():
        return f"Error: file not found: {path}"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return f"Error: cannot read file: {e}"
    start = int(args.get("start_line", 1))
    end = int(args.get("end_line", len(lines)))
    start = max(1, start)
    end = min(len(lines), max(start, end))
    body = "\n".join(f"{i}: {lines[i-1]}" for i in range(start, end + 1))
    return f"{path} ({len(lines)} lines total)\n{body}"


@tool(
    "write_file",
    "Write/overwrite a file with full content. Creates parent directories if needed.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Destination file path"},
            "content": {"type": "string", "description": "Full file content"},
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
        return f"Error: cannot write file: {e}"
    return f"OK: wrote {len(content)} characters to {path}"


@tool(
    "edit_file",
    "Edit a file by replacing old text with new text (single string replacement).",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "old_string": {"type": "string", "description": "Exact text to replace"},
            "new_string": {"type": "string", "description": "Replacement text"},
        },
        "required": ["path", "old_string", "new_string"],
    },
)
def edit_file(args: dict, ctx: ToolContext) -> str:
    path = ctx.require_allowed(ctx.resolve(str(args["path"])))
    old = str(args.get("old_string", ""))
    new = str(args.get("new_string", ""))
    if not old:
        return "Error: old_string must not be empty"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error: cannot read file: {e}"
    count = content.count(old)
    if count == 0:
        return f"Error: old_string not found in {path}"
    if count > 1:
        return f"Error: old_string found {count} times; make old_string more unique."
    content = content.replace(old, new, 1)
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Error: cannot write file: {e}"
    return f"OK: 1 replacement in {path}"


@tool(
    "list_dir",
    "List directory contents (name, size, type). Without arguments uses working_dir.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list (optional)"},
            "pattern": {"type": "string", "description": "Filename filter (optional)"},
        },
        "required": [],
    },
)
def list_dir(args: dict, ctx: ToolContext) -> str:
    raw = str(args.get("path") or ".")
    path = ctx.require_allowed(ctx.resolve(raw))
    if not path.is_dir():
        return f"Error: not a directory: {path}"
    pattern = str(args.get("pattern") or "")
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    lines = []
    for e in entries:
        if pattern and pattern not in e.name:
            continue
        kind = "DIR" if e.is_dir() else "file"
        size = "" if e.is_dir() else f"{e.stat().st_size}B"
        lines.append(f"{kind:4} {e.name:40} {size}")
    return f"{path} ({len(lines)} entries)\n" + "\n".join(lines)