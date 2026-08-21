"""Search tools: regex text search and glob filename search."""
from __future__ import annotations

import os
import re
from itertools import chain
from pathlib import Path

from termux_agent.tools.base import ToolContext, tool

_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".parcel-cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".turbo",
    "__pycache__",
    "coverage",
    "node_modules",
    "dist",
    "build",
    "target",
    ".venv",
    "venv",
}


def _iter_files(root: Path):
    """Yield repository files lazily while pruning generated directories."""
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in _IGNORED_DIRS]
        base = Path(current)
        for name in files:
            yield base / name


@tool(
    "grep_file",
    "Search for a regex pattern inside files/directories. Results formatted as path:line:content.",
    {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "File or directory to search (default: working_dir)"},
            "include": {"type": "string", "description": "Extension/filename filter (e.g. '*.py', optional)"},
            "max_results": {"type": "integer", "description": "Maximum number of results (default 100)"},
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
        return f"Error: invalid regex: {e}"
    if path.is_file():
        targets = (path,)
    elif path.is_dir():
        targets = _iter_files(path)
    else:
        return f"Error: path not found: {path}"
    results: list[str] = []
    for t in targets:
        if t.is_dir():
            continue
        if include and not t.match(include) and not t.name.endswith(include):
            continue
        try:
            with t.open("r", encoding="utf-8", errors="ignore") as handle:
                for i, raw_line in enumerate(handle, 1):
                    line = raw_line.rstrip("\r\n")
                    if rx.search(line):
                        results.append(f"{t}:{i}: {line}")
                        if len(results) >= max_results:
                            break
                    if len(results) >= max_results:
                        break
        except OSError:
            continue
        if len(results) >= max_results:
            break
    if not results:
        return "No results."
    return "\n".join(results[:max_results])


@tool(
    "glob_find",
    "Find files/directories by glob pattern (e.g. '**/*.py').",
    {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, relative to working_dir"},
            "max_results": {"type": "integer", "description": "Maximum number of results (default 200)"},
        },
        "required": ["pattern"],
    },
)
def glob_find(args: dict, ctx: ToolContext) -> str:
    pattern = str(args.get("pattern", "**/*"))
    max_results = int(args.get("max_results", 200))
    base = ctx.working_dir
    try:
        matches: list[Path] = []
        # Path.glob("**/...") walks dependency and generated directories even
        # when none of their results are useful. Reuse the pruned walker used by
        # grep and match relative paths while traversing lazily.
        for current, dirs, files in os.walk(base):
            dirs[:] = [name for name in dirs if name not in _IGNORED_DIRS]
            current_path = Path(current)
            for name in chain(dirs, files):
                path = current_path / name
                relative = path.relative_to(base)
                matched = relative.match(pattern)
                if not matched and pattern.startswith("**/"):
                    matched = relative.match(pattern[3:])
                if matched and ctx.is_allowed(path):
                    matches.append(path)
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
    except (ValueError, OSError) as e:
        return f"Error: {e}"
    if not matches:
        return "No results."
    lines = [f"{p.relative_to(base) if p.is_relative_to(base) else p}" for p in matches]
    return "\n".join(lines)
