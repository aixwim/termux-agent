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
    agent_name: str | None = None,
    working_dir: str | None = None,
    temperature: float | None = None,
    max_tool_rounds: int | None = None,
) -> Agent:
    name = provider_name or cfg.get("provider", "zen")
    provider = create_provider(name, cfg, model)
    if working_dir:
        from pathlib import Path

        cwd = Path(working_dir).expanduser().resolve()
        cwd.mkdir(parents=True, exist_ok=True)
    else:
        cwd = resolve_working_dir(cfg)
    ctx = ToolContext(
        working_dir=cwd,
        max_output_chars=int(cfg.get("max_output_chars", 60000)),
        command_timeout=int(cfg.get("command_timeout", 60)),
        confirm_commands=not auto_accept and bool(cfg.get("confirm_commands", True)),
    )
    if cfg.get("allow_storage"):
        from termux_agent.config import detect_storage_roots

        ctx._allowed_dirs = [*ctx._allowed_dirs, *detect_storage_roots()]
    agent_key = agent_name or cfg.get("agent", "root")
    agent_spec = cfg.get("agents", {}).get(agent_key)
    if agent_spec is None:
        raise ConfigError(f"Agent '{agent_key}' tidak dikenal. Tersedia: {', '.join(cfg.get('agents', {}))}")
    return Agent(
        provider,
        ctx,
        max_tool_rounds=int(max_tool_rounds or cfg.get("max_tool_rounds", 20)),
        temperature=float(temperature if temperature is not None else cfg.get("temperature", 0.7)),
        agent_spec=agent_spec,
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
    agent_name: str | None = None,
    working_dir: str | None = None,
    temperature: float | None = None,
    max_tool_rounds: int | None = None,
) -> int:
    agent = build_agent(cfg, provider, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds)
    from termux_agent.ui.renderer import render_answer, render_tool_use

    render_info(
        f"provider: {agent.provider.name} | model: {agent.provider.model} | "
        f"agent: {agent_name or cfg.get('agent', 'root')} | cwd: {agent.ctx.working_dir}"
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


def cmd_resume(
    cfg: dict,
    session_ref: str | None,
    prompt: str,
    auto_accept: bool = False,
    agent_name: str | None = None,
    working_dir: str | None = None,
    temperature: float | None = None,
    max_tool_rounds: int | None = None,
) -> int:
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
    agent = build_agent(cfg, provider_name, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds)
    agent.messages = [{"role": "system", "content": agent.system_prompt}] + history
    if prompt:
        answer = agent.run(prompt, on_tool_use=render_tool_use)
        render_answer(answer)
        return 0
    Repl(agent, provider_name=provider_name, model=agent.provider.model, agent_name=agent_name).run()
    return 0


def cmd_list_providers(cfg: dict) -> int:
    for name, pc in cfg.get("providers", {}).items():
        models = ", ".join(pc.get("models") or [])
        render_info(f"{name:12} {pc.get('type'):16} models: {models}")
    return 0


def cmd_list_agents(cfg: dict) -> int:
    for name, spec in cfg.get("agents", {}).items():
        tools = spec.get("tools") or []
        label = "semua tool" if not tools else ", ".join(tools)
        render_info(f"{name:10} {spec.get('description', '')}  [{label}]")
    return 0


def cmd_doctor(cfg: dict, network: bool = False) -> int:
    """Diagnostik lingkungan: versi, Termux, config, PATH, koneksi provider."""
    import os
    import platform
    import shutil
    import sys as _sys

    issues = 0
    try:
        import termux_agent.tools.base as tbase

        n_tools = len(tbase.tool_specs())
    except Exception:  # noqa: BLE001
        n_tools = 0

    def ok(label: str, detail: str = "") -> None:
        render_info(f"  [OK]  {label}" + (f": {detail}" if detail else ""))

    def warn(label: str, detail: str = "") -> None:
        nonlocal issues
        issues += 1
        render_error(f"  [!!]  {label}" + (f": {detail}" if detail else ""))

    render_info("== lingkungan ==")
    ok("python", f"{_sys.version.split()[0]} ({_sys.executable})")
    ok("platform", platform.platform())
    is_termux = "TERMUX_VERSION" in os.environ or os.path.isdir("/data/data/com.termux")
    if is_termux:
        ok("termux", "terdeteksi")
    else:
        warn("termux", "tidak terdeteksi — mungkin bukan Termux")

    render_info("== konfigurasi ==")
    ok("config", str(CONFIG_FILE))
    try:
        render_info(f"  provider: {cfg.get('provider')} | model: {cfg.get('model')} | agent: {cfg.get('agent')}")
    except Exception:  # noqa: BLE001
        pass
    try:
        cwd = resolve_working_dir(cfg)
        if os.access(cwd, os.W_OK):
            ok("working_dir dapat ditulis", str(cwd))
        else:
            warn("working_dir", f"tidak dapat ditulis: {cwd}")
    except Exception as e:  # noqa: BLE001
        warn("working_dir", str(e))
    if cfg.get("allow_storage"):
        from termux_agent.config import detect_storage_roots

        roots = detect_storage_roots()
        ok("storage roots", ", ".join(map(str, roots)) or "tidak ada")

    render_info("== PATH & tools ==")
    for name in ("git", "pip", "python"):
        path = shutil.which(name)
        ok(name, path or "TIDAK ADA") if path else warn(name, "tidak ditemukan di PATH")
    ok("tool terdaftar", str(n_tools))

    render_info("== provider ==")
    pname = cfg.get("provider", "zen")
    pc = cfg.get("providers", {}).get(pname, {})
    ok("provider aktif", f"{pname} ({pc.get('type')})")
    if pc.get("api_key_env"):
        key = os.environ.get(pc["api_key_env"])
        model = cfg.get("model", "")
        is_free = pname == "zen" and "free" in str(model)
        if key:
            ok("api key", f"{pc['api_key_env']} terisi ({key[:4]}...)")
        elif is_free:
            ok("api key", f"{pc['api_key_env']} kosong — tidak wajib untuk model free zen")
        else:
            warn("api key", f"{pc['api_key_env']} kosong — pakai env var atau isi di config")
    if network and pc.get("base_url"):
        import urllib.request

        try:
            req = urllib.request.Request(pc["base_url"], method="HEAD")
            urllib.request.urlopen(req, timeout=10).close()
            ok("koneksi", f"{pc['base_url']} terjangkau")
        except Exception as e:  # noqa: BLE001
            warn("koneksi", f"{pc['base_url']}: {type(e).__name__}")

    from termux_agent.session import list_sessions

    ok("sesi tersimpan", str(len(list_sessions())))
    render_info("\nJika ada tanda [!!], jalankan kembali dengan TERMUX_AGENT_DEBUG=1 untuk log detail.")
    return 1 if issues else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="termux-agent",
        description="CLI coding agent untuk Termux, seperti opencode.",
    )
    parser.add_argument("--version", action="version", version=f"termux-agent {__version__}")
    parser.add_argument("--provider", help="Nama provider (mis. openai, anthropic, ollama)")
    parser.add_argument("--model", help="Nama model (mengganti default config)")
    parser.add_argument("--agent", help="Nama sub-agent (mis. explore, coder, shell)")
    parser.add_argument("--cwd", help="Direktori kerja (mengganti working_dir config)")
    parser.add_argument("--temperature", type=float, help="Suhu sampling (0.0-2.0)")
    parser.add_argument("--max-tool-rounds", type=int, help="Batas iterasi tool per pesan")
    parser.add_argument("--verbose", action="store_true", help="Log request/response provider (setara TERMUX_AGENT_DEBUG=1)")
    parser.add_argument("prompt", nargs="*", help="Prompt one-shot (tanpa argumen = mode interaktif)")
    parser.add_argument("--init", action="store_true", help="Buat config.example -> ~/.termux-agent/config.yaml")
    parser.add_argument("--sessions", action="store_true", help="Daftar sesi tersimpan")
    parser.add_argument("--list-providers", action="store_true", help="Daftar preset provider")
    parser.add_argument("--list-agents", action="store_true", help="Daftar sub-agent tersedia")
    parser.add_argument("--doctor", action="store_true", help="Diagnostik lingkungan & config")
    parser.add_argument("--doctor-network", action="store_true", help="Sertakan cek koneksi provider (butuh internet)")
    parser.add_argument(
        "--install-completion",
        nargs="?",
        const="bash",
        metavar="SHELL",
        help="Pasang auto-completion ke .bashrc/.zshrc (default bash)",
    )
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

    if args.doctor or args.doctor_network:
        return cmd_doctor(cfg, network=args.doctor_network)

    if args.verbose:
        import os

        os.environ["TERMUX_AGENT_DEBUG"] = "1"

    if args.list_providers:
        return cmd_list_providers(cfg)
    if args.list_agents:
        return cmd_list_agents(cfg)
    if args.install_completion:
        from termux_agent.completion import install

        shell = args.install_completion.lower()
        try:
            rc = install(shell)
            render_info(f"Auto-completion {shell} dipasang di {rc}. Buka terminal baru atau jalankan 'source {rc}'.")
        except ValueError as e:
            render_error(f"Error: {e}")
            return 1
        return 0

    prompt = " ".join(args.prompt).strip()
    if args.resume:
        return cmd_resume(
            cfg,
            args.resume,
            prompt,
            auto_accept=args.yes,
            agent_name=args.agent,
            working_dir=args.cwd,
            temperature=args.temperature,
            max_tool_rounds=args.max_tool_rounds,
        )

    if prompt:
        return cmd_one_shot(
            cfg,
            prompt,
            args.provider,
            args.model,
            auto_accept=args.yes,
            agent_name=args.agent,
            working_dir=args.cwd,
            temperature=args.temperature,
            max_tool_rounds=args.max_tool_rounds,
        )

    provider_key = args.provider or cfg.get("provider", "zen")
    try:
        agent = build_agent(
            cfg,
            provider_key,
            args.model,
            auto_accept=args.yes,
            agent_name=args.agent,
            working_dir=args.cwd,
            temperature=args.temperature,
            max_tool_rounds=args.max_tool_rounds,
        )
    except (ConfigError, KeyError) as e:
        render_error(f"Error: {e}\nJalankan 'termux-agent --init' dulu, lalu isi API key.")
        return 1
    if args.yes:
        render_info("Mode --yes: semua konfirmasi dilewati otomatis.")
    try:
        Repl(
            agent,
            provider_name=provider_key,
            model=agent.provider.model,
            agent_name=args.agent or cfg.get("agent", "root"),
        ).run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())