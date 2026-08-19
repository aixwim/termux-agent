"""Loop utama agent: chat -> (tool-call -> jalankan tool) -> selesai."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Iterable

from termux_agent.providers.base import Provider, ProviderError, StreamEvent
from termux_agent.tools.base import ToolContext, run_tool, tool_specs

SYSTEM_PROMPT = """Kamu adalah termux-agent, asisten coding yang berjalan di Termux (Android).
Kamu membantu pengguna menulis, membaca, mengedit, dan menjalankan perintah di perangkat mereka.

Aturan:
- Gunakan tool hanya bila diperlukan. Untuk pertanyaan umum, jawab langsung.
- Selalu pakai path relatif terhadap working_dir bila memungkinkan.
- Jangan menjalankan perintah destruktif (rm -rf, format, dsb) tanpa konfirmasi pengguna.
- Bila perintah butuh konfirmasi dan ditolak, jangan mengulanginya.
- Output yang terpotong ("[output terpotong]") menandakan hasil dibatasi; lakukan pencarian yang lebih spesifik.
- Jawab dalam bahasa yang sama dengan pertanyaan pengguna.
- Saat selesai, ringkas singkat apa yang kamu ubah atau jalankan.
"""

# Nama file aturan proyek yang otomatis dibaca (seperti AGENTS.md di opencode).
RULES_FILES = ("AGENTS.md", "CLAUDE.md", ".termux-agent/rules.md")


def load_rules(working_dir: Path) -> str:
    """Baca file aturan proyek dari working_dir (dan parent sampai $HOME)."""
    parts: list[str] = []
    home = Path.home()
    start = working_dir.resolve()
    for directory in (start, *start.parents):
        for name in RULES_FILES:
            f = directory / name
            if f.is_file():
                try:
                    parts.append(f"[Aturan dari {f.relative_to(start) if f.is_relative_to(start) else f}]\n{f.read_text(encoding='utf-8', errors='replace').strip()}")
                except OSError:
                    continue
        if directory == home:
            break
    return "\n\n".join(parts)


def build_system_prompt(extra_rules: str = "") -> str:
    if extra_rules.strip():
        return SYSTEM_PROMPT + "\n\n" + extra_rules
    return SYSTEM_PROMPT


class Agent:
    def __init__(
        self,
        provider: Provider,
        ctx: ToolContext,
        max_tool_rounds: int = 20,
        temperature: float = 0.7,
        system_prompt: str | None = None,
    ) -> None:
        self.provider = provider
        self.ctx = ctx
        self.max_tool_rounds = max_tool_rounds
        self.temperature = temperature
        self.system_prompt = system_prompt or build_system_prompt(load_rules(ctx.working_dir))
        self.messages: list[dict] = [{"role": "system", "content": self.system_prompt}]

    @property
    def tools(self) -> list:
        return tool_specs()

    def run(
        self,
        user_input: str,
        on_text_delta: Callable[[str], None] | None = None,
        on_tool_use: Callable[[str, str], None] | None = None,
    ) -> str:
        """Kirim satu pesan pengguna dan jalankan loop tool-call sampai selesai.
        Mengembalikan teks jawaban akhir."""
        self.messages.append({"role": "user", "content": user_input})
        for _round in range(self.max_tool_rounds):
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            try:
                events: Iterable[StreamEvent] = self.provider.stream(
                    self.messages, tools=self.tools, temperature=self.temperature
                )
                for ev in events:
                    if ev.kind == "text_delta":
                        text_parts.append(ev.text)
                        if on_text_delta:
                            on_text_delta(ev.text)
                    elif ev.kind == "tool_calls":
                        tool_calls = ev.tool_calls
            except ProviderError as e:
                self.messages.append({"role": "assistant", "content": f"[error] {e}"})
                return f"Error: {e}"
            text = "".join(text_parts)

            if not tool_calls:
                self.messages.append({"role": "assistant", "content": text})
                if not text.strip():
                    return "(model mengembalikan jawaban kosong — coba ajukan ulang dengan pertanyaan lebih spesifik)"
                return text

            self.messages.append(
                {"role": "assistant", "content": text, "tool_calls": tool_calls}
            )
            for tc in tool_calls:
                name = tc.get("name", "")
                raw_args = tc.get("arguments", "")
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {"value": args}
                if on_tool_use:
                    on_tool_use(name, json.dumps(args, ensure_ascii=False)[:200])
                result = run_tool(name, args, self.ctx)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result,
                    }
                )
        return "(mencapai batas maksimal putaran tool; hentikan agar tidak berulang)"