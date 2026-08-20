"""Rendering output with rich: markdown, streaming, tool panels."""
from __future__ import annotations

import os
from typing import Any

_console_instance = None


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

        _console_instance = Console(highlight=False, soft_wrap=False)
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


def disable_color() -> None:
    """Disable ANSI colors on the shared console (--no-color / NO_COLOR)."""
    _console().no_color = True


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
        console.print(_markdown(text))
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
    line.append("  ◆ ", style="bold cyan")
    line.append(name, style="bold bright_cyan")
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


def render_info(msg: str) -> None:
    console = _console()
    stripped = msg.strip()
    if stripped.startswith("== ") and stripped.endswith(" ==") and not prefer_plain():
        console.rule(stripped[3:-3], style="dim cyan")
    elif stripped.startswith("[OK]"):
        console.print(_text(msg, style="green"))
    else:
        console.print(_text(msg, style="dim"))


def render_error(msg: str) -> None:
    console = _console()
    stripped = msg.strip()
    if stripped.startswith("[!!]") or stripped.lower().startswith("error:"):
        console.print(_text(msg, style="bold red"))
        return
    line = _text()
    line.append("error  ", style="bold white on red")
    line.append(f" {msg}", style="red")
    console.print(line)


def render_banner(provider: str, model: str, agent: str, cwd: object) -> None:
    """Render a compact, phone-friendly REPL banner."""
    console = _console()
    if prefer_plain():
        console.print(_text(f"termux-agent  {provider} / {model}  [{agent}]"))
        console.print(_text(f"cwd: {cwd}  ·  /help for commands"))
        return
    body = _text()
    body.append(provider, style="bold cyan")
    body.append(" / ", style="dim")
    body.append(model, style="bright_white")
    body.append(f"  [{agent}]\n", style="magenta")
    body.append(str(cwd), style="dim")
    body.append("  ·  /help", style="dim cyan")
    console.print(_panel(body, title=_text("termux-agent", style="bold cyan"), border_style="cyan", padding=(0, 1), expand=False))


class PlainStreamPrinter:
    """Print streaming deltas as plain text (for narrow / low-footprint terminals)."""

    def __init__(self) -> None:
        self._buf = ""
        self.streamed_chars = 0

    def feed(self, delta: str) -> None:
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
