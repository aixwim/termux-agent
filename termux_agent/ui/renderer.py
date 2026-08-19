"""Rendering output dengan rich: markdown, streaming, panel tool."""
from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

console = Console(highlight=False, soft_wrap=True)


def render_answer(text: str) -> None:
    if not text:
        return
    if text.strip().startswith("Error:"):
        console.print(Text(text, style="bold red"))
        return
    try:
        console.print(Markdown(text))
    except Exception:  # noqa: BLE001
        console.print(text)


def render_tool_use(name: str, args_preview: str) -> None:
    console.print(
        Panel(
            Text(f"{name}  {args_preview}", style="cyan"),
            title="tool",
            border_style="blue",
            expand=False,
        )
    )


def render_code(code: str, language: str = "text") -> None:
    try:
        console.print(Syntax(code, language, theme="monokai", word_wrap=True))
    except Exception:  # noqa: BLE001
        console.print(code)


def render_info(msg: str) -> None:
    console.print(Text(msg, style="dim"))


def render_error(msg: str) -> None:
    console.print(Text(msg, style="bold red"))


class StreamPrinter:
    """Cetak teks delta streaming; render markdown ketika kalimat lengkap."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> None:
        self._buffer += delta
        self.console_print_accumulated()

    def console_print_accumulated(self) -> None:
        # print chunk terakhir yang belum dirender sebagai teks polos
        pass

    def flush(self) -> None:
        if self._buffer.strip():
            console.print(Markdown(self._buffer))
        self._buffer = ""


class PlainStreamPrinter:
    """Cetak delta streaming sebagai teks polos (untuk terminal sempit / hemat)."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> None:
        self._buf += delta
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            console.print(line, end="", style="bright_white")

    def flush(self) -> None:
        if self._buf:
            console.print(self._buf, style="bright_white")
            self._buf = ""