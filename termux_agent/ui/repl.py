"""Interactive REPL for termux-agent."""
from __future__ import annotations

import sys
from pathlib import Path

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
Special commands (start with /):
  /exit, /quit    quit
  /new            start a new session
  /help           show this help
  /provider NAME  switch provider (e.g. /provider groq)
  /model MODEL    switch model (e.g. /model gpt-4o-mini)
  /cwd            show the working directory
  /sessions       list saved sessions
  /resume [ID]    resume a session (ID optional, default: latest)
  /compact        summarize the session history to save context
  /agent [NAME]   view/switch sub-agent (explore, coder, shell, ...)
  /export [PATH]  export the conversation to Markdown
  /copy           copy the last answer to the clipboard
Type a normal message to ask; Ctrl+C to cancel."""


def make_prompt_session(history_file: str) -> PromptSession:
    (CONFIG_DIR / "history").mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(CONFIG_DIR / "history" / history_file)),
        style=PROMPT_STYLE,
    )


class Repl:
    def __init__(
        self,
        agent: Agent,
        provider_name: str,
        model: str,
        agent_name: str = "root",
    ) -> None:
        self.agent = agent
        self.provider_name = provider_name
        self.model = model
        self.agent_name = agent_name
        self.session = Session(provider_name=provider_name, model=model)
        self._last_answer = ""

    def _confirm(self, command: str) -> bool:
        try:
            from prompt_toolkit import prompt

            ans = prompt(f"  Run this command? [y/N]  {command}\n> ", default="n")
            return ans.strip().lower() in ("y", "yes")
        except KeyboardInterrupt:
            return False

    def _attach_confirm(self) -> None:
        self.agent.ctx.confirm = self._confirm

    def _banner(self) -> None:
        render_info(
            f"termux-agent | provider: {self.provider_name} | model: {self.model} | "
            f"agent: {self.agent_name} | cwd: {self.agent.ctx.working_dir}\nType /help for help."
        )

    def run(self) -> None:
        if sys.stdin.isatty():
            self._attach_confirm()
            self._banner()
            self._run_tty()
        else:
            # Pipe mode: interactive confirmation is impossible -> refuse everything.
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
        """Fallback when stdin is not a terminal (e.g. piped)."""
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
            render_info("New session started.")
        elif c == "/provider":
            if not rest:
                render_error("Usage: /provider NAME")
                return False
            self._switch_provider(rest)
        elif c == "/model":
            if not rest:
                render_error("Usage: /model NAME")
                return False
            self.model = rest
            self.agent.provider.model = rest
            self.session.model = rest
            render_info(f"Model switched to: {rest}")
        elif c == "/cwd":
            render_info(f"working_dir: {self.agent.ctx.working_dir}")
        elif c == "/sessions":
            from termux_agent.session import list_sessions

            sessions = list_sessions()
            if not sessions:
                render_info("No sessions yet.")
            else:
                for s in sessions[:10]:
                    render_info(f"  {s.name} ({s.stat().st_size}B)")
        elif c == "/resume":
            self._resume(rest)
        elif c == "/compact":
            self._compact()
        elif c == "/agent":
            self._switch_agent(rest)
        elif c == "/export":
            self._export(rest)
        elif c == "/copy":
            self._copy_last()
        else:
            render_error(f"Unknown command: {c}")
        return False

    def _switch_agent(self, name: str) -> None:
        from termux_agent.config import load_config

        name = name.strip()
        if not name:
            self._list_agents()
            return
        cfg = load_config()
        spec = cfg.get("agents", {}).get(name)
        if spec is None:
            render_error(
                f"Unknown agent '{name}'. Available: {', '.join(cfg.get('agents', {}))}"
            )
            return
        self.agent.set_agent(spec)
        self.agent_name = name
        self.session = Session(provider_name=self.provider_name, model=self.model)
        render_info(
            f"Agent switched to: {name} - {spec.get('description', '')} "
            f"(tools restricted to {len(self.agent.tools)})."
        )

    def _list_agents(self) -> None:
        from termux_agent.config import load_config

        for name, spec in load_config().get("agents", {}).items():
            tools = spec.get("tools") or []
            label = "all tools" if not tools else ", ".join(tools)
            render_info(f"  {name:8} {spec.get('description', '')}  [{label}]")

    def _export(self, dest: str) -> None:
        import json

        from termux_agent.config import CONFIG_DIR

        lines = [
            "# termux-agent - conversation export",
            "",
            f"- provider: {self.provider_name}",
            f"- model: {self.model}",
            f"- agent: {self.agent_name}",
            f"- message count: {len([m for m in self.agent.messages if m.get('role') != 'system'])}",
            "",
        ]
        for m in self.agent.messages:
            role = m.get("role")
            if role == "system":
                continue
            content = m.get("content", "")
            lines += [f"## {role}", ""]
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    lines += [f"```json\n{json.dumps(tc, indent=2, ensure_ascii=False)}\n```", ""]
            elif role == "tool":
                lines += [f"```\n{content}\n```", ""]
            else:
                lines += [str(content), ""]
        if dest:
            out = Path(dest).expanduser()
        else:
            from datetime import datetime

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            out = CONFIG_DIR / "exports" / f"session-{stamp}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        render_info(f"Exported to: {out}")

    def _copy_last(self) -> None:
        import shutil
        import subprocess

        if not self._last_answer:
            render_error("No answer to copy yet.")
            return
        clip = shutil.which("termux-clipboard-set") or shutil.which("xclip") or shutil.which("pbcopy")
        if clip:
            proc = subprocess.run([clip], input=self._last_answer.encode(), capture_output=True)
            if proc.returncode == 0:
                render_info("Last answer copied to the clipboard.")
            else:
                render_error("Failed to copy to the clipboard.")
        else:
            render_error("Clipboard unavailable. Install termux-api (pkg install termux-api) or use /export.")

    def _compact(self) -> None:
        render_info("Summarizing session history...")
        summary = self.agent.compact()
        if not summary:
            render_info("Nothing to summarize (conversation is still short).")
        elif summary.startswith("(compact failed"):
            render_error(summary)
        else:
            render_info(f"Session summarized; {len(self.agent.messages)} messages remain.")

    def _resume(self, ref: str) -> None:
        from termux_agent.cli import find_session
        from termux_agent.config import load_config

        found = find_session(ref or None)
        if not found:
            render_error("Session not found.")
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
            render_error(f"Failed to create provider: {e}")
            return
        self.provider_name = provider_name
        self.model = provider.model
        self.agent.provider = provider
        self.agent.messages = [{"role": "system", "content": self.agent.system_prompt}] + history
        self.session = Session(provider_name=provider_name, model=provider.model)
        render_info(f"Resuming session {path.stem} ({len(history)} messages)")

    def _switch_provider(self, name: str) -> None:
        from termux_agent.config import load_config
        from termux_agent.providers import create_provider

        try:
            cfg = load_config()
            provider = create_provider(name, cfg, self.model)
        except Exception as e:  # noqa: BLE001
            render_error(f"Failed to switch provider: {e}")
            return
        self.provider_name = name
        self.agent.provider = provider
        self.agent.messages = [{"role": "system", "content": self.agent.system_prompt}]
        self.session = Session(provider_name=name, model=provider.model)
        self.model = provider.model
        render_info(f"Provider switched to: {name} / {provider.model}")

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
        self._last_answer = answer
        self.session.append({"role": "assistant", "content": answer})