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
    start = int(args.get("start_line", 1))
    start = max(1, start)
    requested_end = args.get("end_line")
    end = max(start, int(requested_end)) if requested_end is not None else None
    selected: list[str] = []
    selected_chars = 0
    total = 0
    truncated = False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for total, raw_line in enumerate(handle, 1):
                if total < start or (end is not None and total > end):
                    continue
                rendered = f"{total}: {raw_line.rstrip(chr(10) + chr(13))}"
                if selected_chars + len(rendered) + 1 <= ctx.max_output_chars:
                    selected.append(rendered)
                    selected_chars += len(rendered) + 1
                else:
                    truncated = True
    except OSError as e:
        return f"Error: cannot read file: {e}"
    body = "\n".join(selected)
    if truncated:
        body += "\n... [selected lines truncated]"
    return f"{path} ({total} lines total)\n{body}"


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
    ctx._snapshot(path)
    try:
        from termux_agent.storage import atomic_write_text

        atomic_write_text(path, content)
    except OSError as e:
        ctx.undo_stack.pop()
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
    ctx._snapshot(path)
    try:
        from termux_agent.storage import atomic_write_text

        atomic_write_text(path, content)
    except OSError as e:
        ctx.undo_stack.pop()
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


@tool(
    "list_tree",
    "Show a recursive tree of a directory (ignores .git). Use depth to limit how deep it goes.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to walk (default: working_dir)"},
            "depth": {"type": "integer", "description": "Max depth (default 3)"},
            "max_entries": {"type": "integer", "description": "Cap on printed entries (default 200)"},
        },
        "required": [],
    },
)
def list_tree(args: dict, ctx: ToolContext) -> str:
    raw = str(args.get("path") or ".")
    path = ctx.require_allowed(ctx.resolve(raw))
    if not path.is_dir():
        return f"Error: not a directory: {path}"
    depth = max(1, min(int(args.get("depth", 3)), 10))
    cap = max(1, int(args.get("max_entries", 200)))
    lines: list[str] = [str(path)]

    def walk(p: Path, level: int) -> None:
        if len(lines) >= cap + 1:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError:
            return
        for e in entries:
            if len(lines) >= cap + 1:
                lines.append("... [tree truncated]")
                return
            if e.name == ".git":
                continue
            prefix = "  " * level
            if e.is_dir():
                lines.append(f"{prefix}{e.name}/")
                if level < depth:
                    walk(e, level + 1)
            else:
                try:
                    size = e.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{prefix}{e.name} ({size}B)")

    walk(path, 0)
    return "\n".join(lines)
