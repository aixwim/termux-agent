"""Entry point CLI: mode interaktif, one-shot, init-config, sessions."""
from __future__ import annotations

import argparse
import sys

from termux_agent import __version__
from termux_agent.agent import Agent
from termux_agent.config import (
    CONFIG_FILE,
    ConfigError,
    ensure_config_file,
    load_config,
    resolve_working_dir,
)
from termux_agent.providers import create_provider
from termux_agent.tools.base import ToolContext
from termux_agent.ui.renderer import render_error, render_info
from termux_agent.ui.repl import Repl


def build_agent(cfg: dict, provider_name: str | None, model: str | None) -> Agent:
    name = provider_name or cfg.get("provider", "openai")
    provider = create_provider(name, cfg, model)
    working_dir = resolve_working_dir(cfg)
    ctx = ToolContext(
        working_dir=working_dir,
        max_output_chars=int(cfg.get("max_output_chars", 60000)),
        command_timeout=int(cfg.get("command_timeout", 60)),
        confirm_commands=bool(cfg.get("confirm_commands", True)),
    )
    return Agent(
        provider,
        ctx,
        max_tool_rounds=int(cfg.get("max_tool_rounds", 20)),
        temperature=float(cfg.get("temperature", 0.7)),
    )


def cmd_init() -> int:
    path = ensure_config_file()
    render_info(f"Konfigurasi dibuat: {path}\n")
    render_info("Langkah berikutnya:\n  1. Edit API key di env var (mis. export OPENAI_API_KEY=...)\n  2. Jalankan: termux-agent")
    return 0


def cmd_one_shot(cfg: dict, prompt: str, provider: str | None, model: str | None) -> int:
    agent = build_agent(cfg, provider, model)
    if not agent.ctx.confirm_commands:
        pass
    from termux_agent.ui.renderer import render_answer, render_tool_use

    render_info(
        f"provider: {agent.provider.name} | model: {agent.provider.model} | cwd: {agent.ctx.working_dir}"
    )
    try:
        answer = agent.run(prompt, on_tool_use=render_tool_use)
    except KeyboardInterrupt:
        render_error("\nDibatalkan.")
        return 130
    render_answer(answer)
    return 0


def cmd_sessions() -> int:
    from termux_agent.session import list_sessions, read_session

    sessions = list_sessions()
    if not sessions:
        render_info("Belum ada sesi di ~/.termux-agent/sessions/.")
        return 0
    for s in sessions[:20]:
        recs = read_session(s)
        first_user = next((r["content"] for r in recs if r.get("role") == "user"), "")
        render_info(f"{s.stem}  [{len(recs)} pesan]  {first_user[:60]}")
    return 0


def cmd_list_providers(cfg: dict) -> int:
    for name, pc in cfg.get("providers", {}).items():
        models = ", ".join(pc.get("models") or [])
        render_info(f"{name:12} {pc.get('type'):16} models: {models}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="termux-agent",
        description="CLI coding agent untuk Termux, seperti opencode.",
    )
    parser.add_argument("--version", action="version", version=f"termux-agent {__version__}")
    parser.add_argument("--provider", help="Nama provider (mis. openai, anthropic, ollama)")
    parser.add_argument("--model", help="Nama model (mengganti default config)")
    parser.add_argument("prompt", nargs="*", help="Prompt one-shot (tanpa argumen = mode interaktif)")
    parser.add_argument("--init", action="store_true", help="Buat config.example -> ~/.termux-agent/config.yaml")
    parser.add_argument("--sessions", action="store_true", help="Daftar sesi tersimpan")
    parser.add_argument("--list-providers", action="store_true", help="Daftar preset provider")
    args = parser.parse_args(argv)

    if args.init:
        return cmd_init()
    if args.sessions:
        return cmd_sessions()

    try:
        cfg = load_config()
    except ConfigError as e:
        render_error(str(e))
        return 1

    # Auto-buat ~/.termux-agent/config.yaml saat run pertama (seperti opencode).
    if not CONFIG_FILE.exists():
        ensure_config_file()
        render_info(
            f"Konfigurasi pertama dibuat di {CONFIG_FILE} — edit bila perlu, "
            "atau langsung pakai (default: OpenCode Zen free)."
        )

    if args.list_providers:
        return cmd_list_providers(cfg)

    prompt = " ".join(args.prompt).strip()
    if prompt:
        return cmd_one_shot(cfg, prompt, args.provider, args.model)

    try:
        agent = build_agent(cfg, args.provider, args.model)
    except (ConfigError, KeyError) as e:
        render_error(f"Error: {e}\nJalankan 'termux-agent --init' dulu, lalu isi API key.")
        return 1
    try:
        Repl(agent, provider_name=agent.provider.name, model=agent.provider.model).run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())