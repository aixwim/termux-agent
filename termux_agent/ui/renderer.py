"""Rendering output with rich: markdown, streaming, tool panels."""
from __future__ import annotations

import os
import re
from contextlib import nullcontext
from typing import Any

_console_instance = None

ACCENT = "bright_cyan"
MUTED = "grey62"
BORDER = "grey35"


def __getattr__(name: str) -> Any:
    if name == "console":
        return _console()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def prefer_plain() -> bool:
    """True on dumb/narrow terminals where rich rendering is slow or useless.

    Termux terminals are often narrow (phone screens) or non-interactive
    (piped output / automation), where ANSI rendering is wasted work.
    """
    term = os.environ.get("TERM", "") or ""
    if term in ("dumb", "unknown", ""):
        return True
    try:
        cols = int(os.environ.get("COLUMNS", "0") or 0)
    except ValueError:
        cols = 0
    if cols and cols < 60:
        return True
    return False


def _console() -> Any:
    """Lazily import and return the shared rich Console (keeps CLI startup fast)."""
    global _console_instance
    if _console_instance is None:
        from rich.console import Console

        _console_instance = Console(highlight=False, soft_wrap=True)
    return _console_instance


def _markdown(text: str) -> Any:
    from rich.markdown import Markdown

    return Markdown(text)


def _panel(*args: Any, **kwargs: Any) -> Any:
    from rich.panel import Panel

    return Panel(*args, **kwargs)


def _syntax(code: str, language: str, theme: str, word_wrap: bool) -> Any:
    from rich.syntax import Syntax

    return Syntax(code, language, theme=theme, word_wrap=word_wrap)


def _text(*args: Any, **kwargs: Any) -> Any:
    from rich.text import Text

    return Text(*args, **kwargs)


def _table(*args: Any, **kwargs: Any) -> Any:
    from rich.table import Table

    return Table(*args, **kwargs)


def disable_color() -> None:
    """Disable ANSI colors on the shared console (--no-color / NO_COLOR)."""
    _console().no_color = True


def activity(message: str = "Thinking") -> Any:
    """Return a transient Rich status for interactive terminals only."""
    console = _console()
    if prefer_plain() or not getattr(console, "is_terminal", False):
        return nullcontext()
    label = _text()
    label.append(f" {message} ", style=f"bold {ACCENT}")
    label.append("Ctrl+C to cancel", style=MUTED)
    return console.status(label, spinner="dots", spinner_style=ACCENT)


def render_answer(text: str) -> None:
    if not text:
        return
    console = _console()
    if text.strip().startswith("Error:"):
        console.print(_text(text, style="bold red"))
        return
    if prefer_plain():
        console.print(text)
        return
    try:
        console.print(
            _panel(
                _markdown(text),
                title=_text(" assistant ", style=f"bold {ACCENT}"),
                title_align="left",
                border_style=BORDER,
                padding=(0, 1),
            )
        )
    except Exception:  # noqa: BLE001
        console.print(text)


def render_tool_use(name: str, args_preview: str) -> None:
    console = _console()
    preview = " ".join(args_preview.split())
    width = max(24, getattr(console, "width", 80))
    try:
        configured_width = int(os.environ.get("COLUMNS", "0") or 0)
    except ValueError:
        configured_width = 0
    if configured_width:
        width = min(width, configured_width)
    limit = max(12, width - len(name) - 10)
    if len(preview) > limit:
        preview = preview[: limit - 1] + "…"
    if prefer_plain():
        console.print(_text(f"[tool] {name}" + (f"  {preview}" if preview else "")))
        return
    line = _text()
    line.append("  ◇ ", style=ACCENT)
    line.append("tool ", style=f"bold {ACCENT}")
    line.append(name, style="bold white")
    if preview:
        line.append(f"  {preview}", style="dim")
    console.print(line)


def render_code(code: str, language: str = "text") -> None:
    console = _console()
    if prefer_plain():
        console.print(code)
        return
    try:
        console.print(_syntax(code, language, "monokai", True))
    except Exception:  # noqa: BLE001
        console.print(code)


def render_help(help_text: str) -> None:
    """Render slash-command help as a compact two-column reference."""
    console = _console()
    if prefer_plain():
        console.print(help_text)
        return

    lines = [line.rstrip() for line in help_text.strip().splitlines()]
    title = lines.pop(0).rstrip(":") if lines else "Commands"
    footer = lines.pop() if lines and not lines[-1].lstrip().startswith("/") else ""
    table = _table(
        title=_text(title, style=f"bold {ACCENT}"),
        title_justify="left",
        box=None,
        padding=(0, 1),
        show_edge=False,
        expand=True,
    )
    table.add_column("command", style=f"bold {ACCENT}", no_wrap=True, ratio=1)
    table.add_column("description", style=MUTED, ratio=2)
    for line in lines:
        parts = re.split(r"\s{2,}", line.strip(), maxsplit=1)
        if len(parts) == 2:
            table.add_row(parts[0], parts[1])
    console.print(table)
    if footer:
        hint = _text("  › ", style=ACCENT)
        hint.append(footer, style=MUTED)
        console.print(hint)


def render_summary(title: str, items: list[tuple[str, object]]) -> None:
    """Render compact key/value metadata for session and configuration views."""
    console = _console()
    if prefer_plain():
        console.print(f"== {title} ==")
        width = max((len(key) for key, _ in items), default=0)
        for key, value in items:
            console.print(f"{key:<{width}}  {value}")
        return

    grid = _table(expand=True, padding=(0, 1), box=None, show_header=False, show_edge=False)
    grid.add_column(style=MUTED, no_wrap=True)
    grid.add_column(style="white", ratio=1)
    for key, value in items:
        grid.add_row(key, str(value))
    console.print(
        _panel(
            grid,
            title=_text(f" {title} ", style=f"bold {ACCENT}"),
            title_align="left",
            border_style=BORDER,
            padding=(0, 1),
        )
    )


def render_table(title: str, columns: list[str], rows: list[list[object]]) -> None:
    """Render safe tabular output without interpreting cell content as markup."""
    console = _console()
    if prefer_plain():
        console.print(f"== {title} ==")
        widths = [len(column) for column in columns]
        for row in rows:
            for index, value in enumerate(row[: len(widths)]):
                widths[index] = max(widths[index], len(str(value)))
        console.print("  ".join(f"{column:<{widths[i]}}" for i, column in enumerate(columns)))
        for row in rows:
            cells = [str(value) for value in row]
            console.print("  ".join(f"{value:<{widths[i]}}" for i, value in enumerate(cells)))
        return

    table = _table(
        title=_text(title, style=f"bold {ACCENT}"),
        title_justify="left",
        box=None,
        padding=(0, 1),
        show_edge=False,
        expand=True,
    )
    for index, column in enumerate(columns):
        table.add_column(
            column,
            style=f"bold {ACCENT}" if index == 0 else MUTED,
            no_wrap=index == 0,
            ratio=1 if index == 0 else 2,
        )
    for row in rows:
        table.add_row(*(_text(str(value)) for value in row))
    console.print(table)


def render_info(msg: str) -> None:
    console = _console()
    stripped = msg.strip()
    if prefer_plain():
        console.print(msg)
    elif stripped.startswith("== ") and stripped.endswith(" =="):
        console.rule(_text(stripped[3:-3].upper(), style=f"bold {MUTED}"), style=BORDER)
    elif stripped.startswith("[OK]"):
        line = _text("  ✓ ", style="bold green")
        line.append(stripped[4:].strip(), style="white")
        console.print(line)
    elif stripped.startswith(("Run:", "Tokens:")):
        line = _text("  › ", style=ACCENT)
        parts = re.split(r"(\s+\|\s+)", stripped)
        for part in parts:
            line.append(part, style=BORDER if "|" in part else MUTED)
        console.print(line)
    elif stripped.startswith("provider:"):
        line = _text("  ● ", style="green")
        line.append(stripped, style=MUTED)
        console.print(line)
    else:
        console.print(_text(msg, style=MUTED))


def render_error(msg: str) -> None:
    console = _console()
    stripped = msg.strip()
    if stripped.startswith("[!!]") or stripped.lower().startswith("error:"):
        console.print(_text(msg, style="bold red"))
        return
    line = _text()
    line.append("  ✕ ", style="bold red")
    line.append("error", style="bold red")
    line.append(f"  {msg}", style="red")
    console.print(line)


def render_banner(provider: str, model: str, agent: str, cwd: object) -> None:
    """Render a compact, phone-friendly REPL banner."""
    console = _console()
    if prefer_plain():
        console.print(_text(f"termux-agent  {provider} / {model}  [{agent}]"))
        console.print(_text(f"cwd: {cwd}  ·  /help for commands"))
        return
    body = _text()
    body.append("● ", style="green")
    body.append(provider, style=f"bold {ACCENT}")
    body.append("  /  ", style=BORDER)
    body.append(model, style="bold white")
    body.append("\n")
    body.append("agent ", style=MUTED)
    body.append(agent, style="magenta")
    body.append("   cwd ", style=MUTED)
    body.append(str(cwd), style="white")
    console.print(
        _panel(
            body,
            title=_text(" termux-agent ", style=f"bold {ACCENT}"),
            subtitle=_text(" /help · Ctrl+C to cancel ", style=MUTED),
            title_align="left",
            subtitle_align="right",
            border_style=BORDER,
            padding=(0, 1),
        )
    )


class PlainStreamPrinter:
    """Print streaming deltas as plain text (for narrow / low-footprint terminals)."""

    def __init__(self) -> None:
        self._buf = ""
        self._started = False
        self.streamed_chars = 0

    def feed(self, delta: str) -> None:
        if not delta:
            return
        if not self._started:
            self._started = True
            if not prefer_plain():
                heading = _text("  assistant ", style=f"bold {ACCENT}")
                _console().print(heading)
        self._buf += delta
        self.streamed_chars += len(delta)
        console = _console()
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            console.print(line, style="bright_white")

    def flush(self) -> None:
        if self._buf:
            _console().print(self._buf, style="bright_white")
            self._buf = ""
        if self._started and not prefer_plain():
            _console().print()
