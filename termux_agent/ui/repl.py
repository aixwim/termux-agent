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

def copy_to_clipboard(text: str) -> bool:
    """Copy text using termux-clipboard-set / xclip / pbcopy. Returns True on success."""
    import shutil
    import subprocess

    clip = shutil.which("termux-clipboard-set") or shutil.which("xclip") or shutil.which("pbcopy")
    if not clip:
        return False
    proc = subprocess.run([clip], input=text.encode(), capture_output=True)
    return proc.returncode == 0

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
  /stats          show token usage of this session
  /undo           revert the most recent file write/edit
  /config         show the active configuration
  /forget [ID]    delete a session (default: this session)
  /models         list available models for the current provider
  /diff           show git working-tree changes & diff summary
  /prompt [TXT]   add a session instruction; /prompt clear removes them; no arg = show
  /remember TXT   store a note in ~/.termux-agent/memory.md (loaded every session)
  /memory         show the persistent memory; /memory clear wipes it
  /cd DIR         change the working directory (and file-access boundary)
  /plan           toggle plan-first mode (propose, approve, then execute)
  /system         show the effective system prompt
  /context        attach/refresh device context (battery/wifi/time) in the system prompt
  /image PATH     attach an image to the next turn
  /attach FILE    read a file's contents into the next turn (repeatable)
  /search TERM    find sessions whose transcript contains the term
  /retry          re-run the last turn
  /quiet          toggle streaming (print the answer only when done)
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
        self._last_user_input = ""
        self._base_prompt = getattr(self.agent, "system_prompt", "")
        self._instructions: list[str] = []
        self.plan_mode = False
        self.quiet = False

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
            user = self._maybe_read_multiline(user, ps)
            user = user.strip()
            if not user:
                continue
            if user.startswith("/"):
                if self._handle_command(user, ps):
                    break
                continue
            self._run_turn(user)

    @staticmethod
    def _maybe_read_multiline(first: str, ps: PromptSession) -> str:
        """Wrap long input in {{ ... }} to send multiple lines at once."""
        stripped = first.lstrip()
        if not stripped.startswith("{{"):
            return first
        if stripped.rstrip().endswith("}}") and stripped.count("{{") == 1:
            return stripped[2:-2].strip()
        lines = [first]
        try:
            while True:
                more = ps.prompt("...> ")
                lines.append(more)
                if more.rstrip().endswith("}}"):
                    break
        except (KeyboardInterrupt, EOFError):
            return first
        body = "\n".join(lines)
        if body.startswith("{{") and body.endswith("}}"):
            return body[2:-2].strip()
        return body

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
            self.agent.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            self._instructions = []
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
        elif c == "/stats":
            self._show_stats()
        elif c == "/undo":
            render_info(self.agent.ctx.undo())
        elif c == "/config":
            render_info(
                f"provider: {self.provider_name} | model: {self.model}\n"
                f"agent: {self.agent_name} | cwd: {self.agent.ctx.working_dir}\n"
                f"temperature: {self.agent.temperature} | max_tool_rounds: {self.agent.max_tool_rounds} | "
                f"max_context_tokens: {self.agent.max_context_tokens} | confirm_commands: {self.agent.ctx.confirm_commands}"
            )
        elif c == "/forget":
            from termux_agent.session import delete_session

            removed = delete_session(rest or self.session.session_id)
            if removed:
                render_info(f"Deleted session: {removed.stem}")
            else:
                render_error(f"Session not found: {rest or 'latest'}")
        elif c == "/models":
            self._list_models()
        elif c == "/diff":
            self._show_diff()
        elif c == "/prompt":
            self._prompt(rest)
        elif c == "/remember":
            self._remember(rest)
        elif c == "/memory":
            from termux_agent.agent import MEMORY_FILE, load_memory

            if rest.strip() == "clear":
                try:
                    MEMORY_FILE.unlink(missing_ok=True)
                except OSError:
                    pass
                render_info("Memory cleared.")
                return False
            mem = load_memory()
            if mem:
                console.print(mem)
            else:
                render_info("Memory is empty. Use /remember TXT to add a note.")
        elif c == "/cd":
            self._cd(rest)
        elif c == "/image":
            if not rest:
                render_error("Usage: /image PATH")
                return False
            img = Path(rest.strip()).expanduser()
            if not img.is_file():
                render_error(f"Image not found: {img}")
                return False
            self._run_turn(f"Describe or analyze this image:\n\n[image: {img}]")
        elif c == "/attach":
            if not rest:
                render_error("Usage: /attach FILE [FILE ...]")
                return False
            parts: list[str] = []
            for f in rest.split():
                p = Path(f).expanduser()
                try:
                    content = p.read_text(encoding="utf-8")
                except OSError as e:
                    render_error(f"Cannot read {p}: {e}")
                    return False
                parts.append(f"<file name={p}>\n{content}\n</file>")
            self._run_turn("Here is the file content:\n\n" + "\n\n".join(parts))
        elif c == "/search":
            self._search(rest.strip())
        elif c == "/retry":
            if not self._last_user_input:
                render_error("No previous turn to retry.")
                return False
            render_info("Re-running the last turn...")
            self._run_turn(self._last_user_input)
        elif c == "/quiet":
            self.quiet = not self.quiet
            render_info(f"Streaming {'OFF' if self.quiet else 'ON'} (answers print when done).")
        elif c == "/plan":
            self.plan_mode = not self.plan_mode
            render_info(f"Plan-first mode {'ON' if self.plan_mode else 'OFF'}.")
        elif c == "/system":
            console.print(self.agent.system_prompt)
        elif c == "/context":
            import re

            from termux_agent.notify import device_context

            ctx = device_context()
            base = re.sub(r"\n\n\[Device context\].*", "", self.agent.system_prompt, flags=re.S)
            self.agent.system_prompt = base + (f"\n\n[Device context]\n{ctx}" if ctx else "")
            if self.agent.messages and self.agent.messages[0].get("role") == "system":
                self.agent.messages[0]["content"] = self.agent.system_prompt
            render_info(f"Device context {'attached' if ctx else 'unavailable'}.")
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
        if not self._last_answer:
            render_error("No answer to copy yet.")
            return
        if copy_to_clipboard(self._last_answer):
            render_info("Last answer copied to the clipboard.")
        else:
            render_error("Clipboard unavailable. Install termux-api (pkg install termux-api) or use /export.")

    def _show_stats(self) -> None:
        u = self.agent.usage
        if not u or not any(u.values()):
            total = sum(len(str(m.get("content", ""))) // 4 for m in self.agent.messages if m.get("content"))
            render_info(f"Provider reports no usage; estimated context so far: ~{total} tokens.")
            return
        render_info(
            f"Tokens: prompt {u.get('prompt_tokens', 0)} | "
            f"completion {u.get('completion_tokens', 0)} | "
            f"total {u.get('total_tokens', 0)}"
        )

    def _list_models(self) -> None:
        from termux_agent.cli import cmd_list_models
        from termux_agent.config import load_config

        cmd_list_models(load_config(), self.provider_name)

    def _search(self, term: str) -> None:
        from termux_agent.session import list_sessions, session_messages

        if not term:
            render_error("Usage: /search TERM")
            return
        term = term.lower()
        found = 0
        for s in list_sessions():
            for rec in session_messages(s):
                content = str(rec.get("content", ""))
                if term in content.lower():
                    snippet = content.strip().replace("\n", " ")[:120]
                    console.print(f"[bold]{s.stem}[/bold]  {snippet}")
                    found += 1
                    break
        if not found:
            render_info(f"No sessions matched '{term}'.")
        else:
            render_info(f"{found} session(s) matched. Use /resume <id> to open one.")

    def _rebuild_system_prompt(self) -> str:
        p = self._base_prompt
        if self._instructions:
            p += "\n\n[Session instruction]\n" + "\n".join(f"- {s}" for s in self._instructions)
        mem = CONFIG_DIR / "memory.md"
        if mem.is_file():
            content = mem.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                p += f"\n\n[Memory]\n{content}"
        return p

    def _remember(self, text: str) -> None:
        if not text:
            render_error("Usage: /remember <text>")
            return
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        mem = CONFIG_DIR / "memory.md"
        with mem.open("a", encoding="utf-8") as f:
            f.write(text.strip() + "\n")
        self.agent.system_prompt = self._rebuild_system_prompt()
        self.agent.messages[0] = {"role": "system", "content": self.agent.system_prompt}
        render_info("Remembered (persists across sessions).")

    def _cd(self, dest: str) -> None:
        from pathlib import Path

        target = Path(dest).expanduser()
        if not target.is_absolute():
            target = self.agent.ctx.working_dir / target
        target = target.resolve()
        if not target.is_dir():
            render_error(f"Not a directory: {target}")
            return
        self.agent.ctx.working_dir = target
        render_info(f"Working directory changed to: {target}")

    def _prompt(self, arg: str) -> None:
        if not arg:
            if self._instructions:
                render_info("Session instructions:")
                for s in self._instructions:
                    render_info(f"  - {s}")
            else:
                render_info("No session instructions set. Use /prompt <text> to add one.")
            return
        if arg.strip().lower() == "clear":
            self._instructions = []
            self.agent.system_prompt = self._rebuild_system_prompt()
            self.agent.messages[0] = {"role": "system", "content": self.agent.system_prompt}
            render_info("Session instructions cleared.")
            return
        self._instructions.append(arg.strip())
        self.agent.system_prompt = self._rebuild_system_prompt()
        self.agent.messages[0] = {"role": "system", "content": self.agent.system_prompt}
        render_info(f"Instruction added ({len(self._instructions)} total).")

    def _show_diff(self) -> None:
        import subprocess

        cwd = str(self.agent.ctx.working_dir)
        try:
            stat = subprocess.run(
                ["git", "-C", cwd, "diff", "--stat"], capture_output=True, text=True, timeout=30
            )
            status = subprocess.run(
                ["git", "-C", cwd, "status", "--short"], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            render_error("Cannot run git here.")
            return
        if status.stdout.strip():
            render_info("Changes in working tree:")
            for line in status.stdout.splitlines()[:30]:
                render_info(f"  {line}")
        else:
            render_info("Working tree is clean (no tracked changes).")
        if stat.stdout.strip():
            render_info("Diff summary:")
            for line in stat.stdout.splitlines()[:30]:
                render_info(f"  {line}")

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
        self._last_user_input = user_input
        self.session.append({"role": "user", "content": user_input})
        printer = PlainStreamPrinter()
        if self.plan_mode:
            self._run_plan_turn(user_input, printer)
            return
        try:
            if self.quiet:
                answer = self.agent.run(user_input, on_tool_use=render_tool_use)
            else:
                answer = self.agent.run(
                    user_input,
                    on_text_delta=printer.feed,
                    on_tool_use=render_tool_use,
                )
        except Exception as e:  # noqa: BLE001
            render_error(f"Error: {e}")
            return
        if not self.quiet:
            printer.flush()
        self._last_answer = answer
        self.session.append({"role": "assistant", "content": answer})

    def _run_plan_turn(self, user_input: str, printer: PlainStreamPrinter) -> None:
        """Plan-first mode: read-only plan, ask approval, then execute."""
        from termux_agent.cli import READONLY_TOOLS

        from termux_agent.ui.renderer import render_answer, render_info

        saved_tools = self.agent.allowed_tools
        self.agent.allowed_tools = set(READONLY_TOOLS)
        render_info("Plan-first mode: producing a read-only plan (no changes yet)...")
        try:
            plan = self.agent.run(
                f"{user_input}\n\nFirst, analyze the request and produce a clear step-by-step plan. "
                "Do NOT modify anything yet - you are in planning mode.",
                on_text_delta=printer.feed,
                on_tool_use=render_tool_use,
            )
        except Exception as e:  # noqa: BLE001
            render_error(f"Error: {e}")
            return
        finally:
            self.agent.allowed_tools = saved_tools
        printer.flush()
        render_info("\n--- PLAN ---")
        render_answer(plan)
        render_info("--- END OF PLAN ---")
        self._last_answer = plan
        self.session.append({"role": "assistant", "content": plan})
        try:
            ok = input("\nExecute this plan? [y/N] > ").strip().lower() in ("y", "yes")
        except (KeyboardInterrupt, EOFError):
            ok = False
        if not ok:
            render_info("Plan not executed. Type your next message (or /plan to leave this mode).")
            return
        try:
            answer = self.agent.run(
                f"Execute the approved plan below, then report the results.\n\n"
                f"APPROVED PLAN:\n{plan}\n\nORIGINAL REQUEST:\n{user_input}",
                on_text_delta=printer.feed,
                on_tool_use=render_tool_use,
            )
        except Exception as e:  # noqa: BLE001
            render_error(f"Error: {e}")
            return
        printer.flush()
        self._last_answer = answer
        self.session.append({"role": "assistant", "content": answer})