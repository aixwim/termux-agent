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
    if prefer_plain():
        console.print(f"tool: {name}  {args_preview}")
        return
    console.print(
        _panel(
            _text(f"{name}  {args_preview}", style="cyan"),
            title="tool",
            border_style="blue",
            expand=False,
        )
    )


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
    _console().print(_text(msg, style="dim"))


def render_error(msg: str) -> None:
    _console().print(_text(msg, style="bold red"))


class StreamPrinter:
    """Print streaming text deltas; render markdown when a sentence completes."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> None:
        self._buffer += delta
        self.console_print_accumulated()

    def console_print_accumulated(self) -> None:
        # print the last unrendered chunk as plain text
        pass

    def flush(self) -> None:
        if self._buffer.strip():
            _console().print(_markdown(self._buffer))
        self._buffer = ""


class PlainStreamPrinter:
    """Print streaming deltas as plain text (for narrow / low-footprint terminals)."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> None:
        self._buf += delta
        console = _console()
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            console.print(line, end="", style="bright_white")

    def flush(self) -> None:
        if self._buf:
            _console().print(self._buf, style="bright_white")
            self._buf = ""