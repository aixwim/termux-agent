"""Core agent loop: chat -> (tool-call -> run tool) -> done."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Iterable

from termux_agent.config import CONFIG_DIR
from termux_agent.providers.base import Provider, ProviderError, StreamEvent
from termux_agent.tools.base import ToolContext, run_tool, tool_specs

SYSTEM_PROMPT = """You are termux-agent, a coding assistant running on Termux (Android).
You help the user write, read, edit, and run commands on their device.

Rules:
- Use tools only when needed. For general questions, answer directly.
- Always prefer paths relative to working_dir when possible.
- Do not run destructive commands (rm -rf, format, etc.) without user confirmation.
- If a command needs confirmation and is refused, do not retry it.
- Truncated output ("[output truncated]") means the result was limited; do a more specific search.
- Answer in the same language as the user's question.
- When done, briefly summarize what you changed or ran.
"""

# Project rule files that are auto-loaded (like AGENTS.md in opencode).
RULES_FILES = ("AGENTS.md", "CLAUDE.md", ".termux-agent/rules.md")


def load_rules(working_dir: Path) -> str:
    """Read project rule files from working_dir (and parents up to $HOME)."""
    parts: list[str] = []
    home = Path.home()
    start = working_dir.resolve()
    for directory in (start, *start.parents):
        for name in RULES_FILES:
            f = directory / name
            if f.is_file():
                try:
                    parts.append(f"[Rules from {f.relative_to(start) if f.is_relative_to(start) else f}]\n{f.read_text(encoding='utf-8', errors='replace').strip()}")
                except OSError:
                    continue
        if directory == home:
            break
    return "\n\n".join(parts)


def build_system_prompt(extra_rules: str = "", agent_prompt: str = "") -> str:
    parts = [SYSTEM_PROMPT]
    if agent_prompt.strip():
        parts.append(f"[Agent role]\n{agent_prompt.strip()}")
    if extra_rules.strip():
        parts.append(extra_rules)
    return "\n\n".join(parts)


MEMORY_FILE = CONFIG_DIR / "memory.md"


def load_memory() -> str:
    """Read the persistent memory file (~/.termux-agent/memory.md), if any."""
    try:
        if MEMORY_FILE.is_file():
            return MEMORY_FILE.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        pass
    return ""


class Agent:
    def __init__(
        self,
        provider: Provider,
        ctx: ToolContext,
        max_tool_rounds: int = 20,
        temperature: float = 0.7,
        system_prompt: str | None = None,
        agent_spec: dict | None = None,
        max_context_tokens: int = 0,
        retries: int = 1,
        retry_backoff: float = 1.0,
    ) -> None:
        self.provider = provider
        self.ctx = ctx
        self.max_tool_rounds = max_tool_rounds
        self.temperature = temperature
        self.max_context_tokens = max_context_tokens
        self.retries = retries
        self.retry_backoff = retry_backoff
        self._compacted_this_turn = False
        self.agent_spec = agent_spec or {}
        self.allowed_tools: set[str] | None = None
        spec_tools = self.agent_spec.get("tools") or []
        if spec_tools:
            self.allowed_tools = set(spec_tools)
        rules = load_rules(ctx.working_dir)
        agent_prompt = str(self.agent_spec.get("prompt", ""))
        self.system_prompt = system_prompt or build_system_prompt(rules, agent_prompt)
        mem = load_memory()
        if mem:
            self.system_prompt += f"\n\n[Memory]\n{mem}"
        self.messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        self.usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _add_usage(self, usage: dict) -> None:
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = usage.get(k)
            if v:
                self.usage[k] = self.usage.get(k, 0) + int(v)

    def set_agent(self, spec: dict | None) -> None:
        """Switch agent role (prompt + tool restrictions) and reset history."""
        self.agent_spec = spec or {}
        spec_tools = self.agent_spec.get("tools") or []
        self.allowed_tools = set(spec_tools) if spec_tools else None
        rules = load_rules(self.ctx.working_dir)
        agent_prompt = str(self.agent_spec.get("prompt", ""))
        self.system_prompt = build_system_prompt(rules, agent_prompt)
        mem = load_memory()
        if mem:
            self.system_prompt += f"\n\n[Memory]\n{mem}"
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @property
    def tools(self) -> list:
        specs = tool_specs()
        if self.allowed_tools is None:
            return specs
        return [s for s in specs if s.name in self.allowed_tools]

    def _with_tools(self, enabled: bool) -> "Agent":
        """Disable all tools for chat mode (enabled=False); otherwise keep agent limits."""
        if not enabled:
            self.allowed_tools = set()
        return self

    def run(
        self,
        user_input: str,
        on_text_delta: Callable[[str], None] | None = None,
        on_tool_use: Callable[[str, str], None] | None = None,
    ) -> str:
        """Send one user message and run the tool-call loop until done.
        Returns the final answer text."""
        self.messages.append({"role": "user", "content": user_input})
        self._compacted_this_turn = False
        models = [self.provider.model, *self.provider.fallback_models]
        try:
            return self._attempt(models, on_text_delta, on_tool_use)
        except ProviderError as e:
            self.messages.append({"role": "assistant", "content": f"[error] {e}"})
            return f"Error: {e}"

    @staticmethod
    def _is_transient(msg: str) -> bool:
        """Transient failures worth retrying on a mobile/flaky network."""
        return "connection failed" in msg or __import__("re").search(r"HTTP 5\d\d", msg) is not None

    def _attempt(
        self,
        models: list[str],
        on_text_delta: Callable[[str], None] | None,
        on_tool_use: Callable[[str, str], None] | None,
    ) -> str:
        import time

        last: ProviderError | None = None
        for i, model in enumerate(models):
            if i:
                self.provider.model = model
            for attempt in range(self.retries + 1):
                try:
                    return self._run_rounds(on_text_delta, on_tool_use)
                except ProviderError as e:
                    last = e
                    msg = str(e)
                    if "429" in msg:
                        break  # rate limited -> try next fallback model
                    if self._is_transient(msg) and attempt < self.retries:
                        time.sleep(self.retry_backoff * (attempt + 1))
                        continue
                    raise  # not transient, or retries exhausted
        assert last is not None
        raise last

    def _run_rounds(
        self,
        on_text_delta: Callable[[str], None] | None,
        on_tool_use: Callable[[str, str], None] | None,
    ) -> str:
        for _round in range(self.max_tool_rounds):
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            events: Iterable[StreamEvent] = self.provider.stream(
                self.messages, tools=self.tools, temperature=self.temperature
            )
            for ev in events:
                if ev.kind == "text_delta":
                    text_parts.append(ev.text)
                    if on_text_delta:
                        on_text_delta(ev.text)
                elif ev.kind == "usage":
                    self._add_usage(ev.usage)
                elif ev.kind == "tool_calls":
                    tool_calls = ev.tool_calls
            if self.max_context_tokens and not self._compacted_this_turn:
                if self.usage.get("total_tokens", 0) >= self.max_context_tokens:
                    if self.compact():
                        self._compacted_this_turn = True
            text = "".join(text_parts)

            if not tool_calls:
                self.messages.append({"role": "assistant", "content": text})
                if not text.strip():
                    return "(model returned an empty response - try rephrasing with a more specific prompt)"
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
        return "(reached the maximum tool rounds; stopping to avoid a loop)"

    def compact(self, keep_recent: int = 4) -> str:
        """Summarize old messages (besides the last N) into one summary,
        then replace them with it to keep context small."""
        if len(self.messages) <= keep_recent + 1:
            return ""
        system = self.messages[0]
        recent = self.messages[-keep_recent:]
        old = self.messages[1:-keep_recent]
        history = "\n".join(
            f"{m.get('role')}: {m.get('content', '')}" for m in old if m.get("content")
        )
        if not history.strip():
            return ""
        prompt = (
            "Summarize the following conversation in the same language as the conversation. "
            "Keep all decisions, files created/changed, commands run, "
            "and important conclusions. Format: a concise paragraph.\n\n" + history
        )
        summary_parts: list[str] = []
        try:
            for ev in self.provider.stream(
                [system, {"role": "user", "content": prompt}],
                tools=None,
                temperature=0.3,
            ):
                if ev.kind == "text_delta":
                    summary_parts.append(ev.text)
                elif ev.kind == "usage":
                    self._add_usage(ev.usage)
        except ProviderError as e:
            return f"(compact failed: {e})"
        summary = "".join(summary_parts).strip()
        if not summary:
            return ""
        self.messages = [
            system,
            {
                "role": "user",
                "content": f"[Summary of previous conversation]\n{summary}",
            },
            *recent,
        ]
        return summary