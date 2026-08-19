"""REPL interaktif untuk termux-agent."""
from __future__ import annotations

import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

from termux_agent.agent import Agent
from termux_agent.config import CONFIG_DIR
from termux_agent.session import Session
from termux_agent.ui.renderer import (
    PlainStreamPrinter,
    console,
    render_error,
    render_info,
    render_tool_use,
)

PROMPT_STYLE = Style.from_dict({"prompt": "bold cyan"})

HELP = """\
Perintah khusus (diawali /):
  /exit, /quit    keluar
  /new            mulai sesi baru
  /help           tampilkan bantuan ini
  /provider NAME  ganti provider (mis. /provider groq)
  /model MODEL    ganti model (mis. /model gpt-4o-mini)
  /cwd            tampilkan direktori kerja
  /sessions       daftar sesi tersimpan
  /resume [ID]    lanjutkan sesi (ID opsional, default terbaru)
  /compact        ringkas riwayat sesi agar hemat konteks
Ketikan pesan biasa untuk bertanya; Ctrl+C untuk membatalkan."""


def make_prompt_session(history_file: str) -> PromptSession:
    (CONFIG_DIR / "history").mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(CONFIG_DIR / "history" / history_file)),
        style=PROMPT_STYLE,
    )


class Repl:
    def __init__(self, agent: Agent, provider_name: str, model: str) -> None:
        self.agent = agent
        self.provider_name = provider_name
        self.model = model
        self.session = Session(provider_name=provider_name, model=model)

    def _confirm(self, command: str) -> bool:
        try:
            from prompt_toolkit import prompt

            ans = prompt(f"  Jalankan perintah ini? [y/N]  {command}\n> ", default="n")
            return ans.strip().lower() in ("y", "yes")
        except KeyboardInterrupt:
            return False

    def _attach_confirm(self) -> None:
        self.agent.ctx.confirm = self._confirm

    def _banner(self) -> None:
        render_info(
            f"termux-agent | provider: {self.provider_name} | model: {self.model} | "
            f"cwd: {self.agent.ctx.working_dir}\nKetik /help untuk bantuan."
        )

    def run(self) -> None:
        if sys.stdin.isatty():
            self._attach_confirm()
            self._banner()
            self._run_tty()
        else:
            # Mode pipe: tidak bisa konfirmasi interaktif -> tolak semua.
            self.agent.ctx.confirm = lambda _cmd: False
            self._banner()
            self._run_piped()

    def _run_tty(self) -> None:
        ps = make_prompt_session(self.provider_name)
        while True:
            try:
                user = ps.prompt("you> ")
            except (KeyboardInterrupt, EOFError):
                console.print()
                break
            user = user.strip()
            if not user:
                continue
            if user.startswith("/"):
                if self._handle_command(user, ps):
                    break
                continue
            self._run_turn(user)

    def _run_piped(self) -> None:
        """Fallback saat stdin bukan terminal (mis. di-pipe)."""
        for line in sys.stdin:
            user = line.strip()
            if not user:
                continue
            if user.startswith("/"):
                if self._handle_command(user, None):
                    break
                continue
            self._run_turn(user)

    def _handle_command(self, cmd: str, ps: PromptSession) -> bool:
        c, _, rest = cmd.partition(" ")
        rest = rest.strip()
        if c in ("/exit", "/quit"):
            return True
        if c == "/help":
            console.print(HELP)
        elif c == "/new":
            self.session = Session(provider_name=self.provider_name, model=self.model)
            self.agent.messages = [
                {"role": "system", "content": self.agent.system_prompt}
            ]
            render_info("Sesi baru dimulai.")
        elif c == "/provider":
            if not rest:
                render_error("Gunakan: /provider NAMA")
                return False
            self._switch_provider(rest)
        elif c == "/model":
            if not rest:
                render_error("Gunakan: /model NAMA")
                return False
            self.model = rest
            self.agent.provider.model = rest
            self.session.model = rest
            render_info(f"Model diganti: {rest}")
        elif c == "/cwd":
            render_info(f"working_dir: {self.agent.ctx.working_dir}")
        elif c == "/sessions":
            from termux_agent.session import list_sessions

            sessions = list_sessions()
            if not sessions:
                render_info("Belum ada sesi.")
            else:
                for s in sessions[:10]:
                    render_info(f"  {s.name} ({s.stat().st_size}B)")
        elif c == "/resume":
            self._resume(rest)
        elif c == "/compact":
            self._compact()
        else:
            render_error(f"Perintah tidak dikenal: {c}")
        return False

    def _compact(self) -> None:
        render_info("Merangkum riwayat sesi...")
        summary = self.agent.compact()
        if not summary:
            render_info("Tidak ada yang perlu diringkas (percakapan masih pendek).")
        elif summary.startswith("(gagal"):
            render_error(summary)
        else:
            render_info(f"Sesi diringkas dari {len(self.agent.messages)} pesan tersisa.")

    def _resume(self, ref: str) -> None:
        from termux_agent.cli import find_session
        from termux_agent.config import load_config

        found = find_session(ref or None)
        if not found:
            render_error("Sesi tidak ditemukan.")
            return
        path, info, history = found
        cfg = load_config()
        provider_name = info.get("provider") or self.provider_name
        if provider_name not in cfg.get("providers", {}):
            provider_name = cfg.get("provider", "zen")
        model = info.get("model") or self.model
        from termux_agent.providers import create_provider

        try:
            provider = create_provider(provider_name, cfg, model)
        except Exception as e:  # noqa: BLE001
            render_error(f"Gagal buat provider: {e}")
            return
        self.provider_name = provider_name
        self.model = provider.model
        self.agent.provider = provider
        self.agent.messages = [{"role": "system", "content": self.agent.system_prompt}] + history
        self.session = Session(provider_name=provider_name, model=provider.model)
        render_info(f"Melanjutkan sesi {path.stem} ({len(history)} pesan)")

    def _switch_provider(self, name: str) -> None:
        from termux_agent.config import load_config
        from termux_agent.providers import create_provider

        try:
            cfg = load_config()
            provider = create_provider(name, cfg, self.model)
        except Exception as e:  # noqa: BLE001
            render_error(f"Gagal ganti provider: {e}")
            return
        self.provider_name = name
        self.agent.provider = provider
        self.agent.messages = [{"role": "system", "content": self.agent.system_prompt}]
        self.session = Session(provider_name=name, model=provider.model)
        self.model = provider.model
        render_info(f"Provider diganti: {name} / {provider.model}")

    def _run_turn(self, user_input: str) -> None:
        self.session.append({"role": "user", "content": user_input})
        printer = PlainStreamPrinter()
        try:
            answer = self.agent.run(
                user_input,
                on_text_delta=printer.feed,
                on_tool_use=render_tool_use,
            )
        except Exception as e:  # noqa: BLE001
            render_error(f"Error: {e}")
            return
        printer.flush()
        self.session.append({"role": "assistant", "content": answer})