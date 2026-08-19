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


def build_agent(
    cfg: dict,
    provider_name: str | None,
    model: str | None,
    auto_accept: bool = False,
) -> Agent:
    name = provider_name or cfg.get("provider", "zen")
    provider = create_provider(name, cfg, model)
    working_dir = resolve_working_dir(cfg)
    ctx = ToolContext(
        working_dir=working_dir,
        max_output_chars=int(cfg.get("max_output_chars", 60000)),
        command_timeout=int(cfg.get("command_timeout", 60)),
        confirm_commands=not auto_accept and bool(cfg.get("confirm_commands", True)),
    )
    if cfg.get("allow_storage"):
        from termux_agent.config import detect_storage_roots

        ctx._allowed_dirs = [*ctx._allowed_dirs, *detect_storage_roots()]
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


def cmd_one_shot(
    cfg: dict,
    prompt: str,
    provider: str | None,
    model: str | None,
    auto_accept: bool = False,
) -> int:
    agent = build_agent(cfg, provider, model, auto_accept)
    from termux_agent.ui.renderer import render_answer, render_tool_use

    render_info(
        f"provider: {agent.provider.name} | model: {agent.provider.model} | cwd: {agent.ctx.working_dir}"
    )
    if auto_accept:
        render_info("Mode --yes: semua konfirmasi dilewati otomatis.")
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


def find_session(session_ref: str | None) -> "tuple[Path, dict, list[dict]] | None":
    """Cari file sesi + info provider + riwayat pesan."""
    from termux_agent.session import latest_session, list_sessions, read_session, session_messages

    path: Path | None = None
    if session_ref and session_ref not in ("latest", ""):
        matches = [s for s in list_sessions() if s.stem.startswith(session_ref)]
        path = matches[-1] if matches else None
    else:
        path = latest_session()
    if not path:
        return None
    recs = read_session(path)
    info = next((r for r in recs if r.get("provider")), {})
    return path, info, session_messages(path)


def cmd_resume(cfg: dict, session_ref: str | None, prompt: str, auto_accept: bool = False) -> int:
    from termux_agent.ui.renderer import render_answer, render_tool_use

    found = find_session(session_ref)
    if not found:
        render_error("Sesi tidak ditemukan. Jalankan 'termux-agent --sessions' untuk daftar.")
        return 1
    path, info, history = found
    render_info(f"Melanjutkan sesi {path.stem} ({len(history)} pesan)")
    provider_name = info.get("provider") or cfg.get("provider", "zen")
    if provider_name not in cfg.get("providers", {}):
        provider_name = cfg.get("provider", "zen")
    model = info.get("model") or None
    agent = build_agent(cfg, provider_name, model, auto_accept=auto_accept)
    agent.messages = [{"role": "system", "content": agent.system_prompt}] + history
    if prompt:
        answer = agent.run(prompt, on_tool_use=render_tool_use)
        render_answer(answer)
        return 0
    Repl(agent, provider_name=provider_name, model=agent.provider.model).run()
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
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        metavar="ID",
        help="Lanjutkan sesi sebelumnya (default: sesi terbaru)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Lewati semua konfirmasi (berbahaya: izinkan perintah & commit apa pun)",
    )
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
    if args.resume:
        return cmd_resume(cfg, args.resume, prompt, auto_accept=args.yes)

    if prompt:
        return cmd_one_shot(cfg, prompt, args.provider, args.model, auto_accept=args.yes)

    provider_key = args.provider or cfg.get("provider", "zen")
    try:
        agent = build_agent(cfg, provider_key, args.model, auto_accept=args.yes)
    except (ConfigError, KeyError) as e:
        render_error(f"Error: {e}\nJalankan 'termux-agent --init' dulu, lalu isi API key.")
        return 1
    if args.yes:
        render_info("Mode --yes: semua konfirmasi dilewati otomatis.")
    try:
        Repl(agent, provider_name=provider_key, model=agent.provider.model).run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())