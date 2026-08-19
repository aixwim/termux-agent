"""CLI entry point: interactive mode, one-shot, init-config, sessions."""
from __future__ import annotations

import argparse
import copy
import sys

import yaml

from termux_agent import __version__
from termux_agent.agent import Agent
from termux_agent.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULTS,
    ConfigError,
    ensure_config_file,
    load_config,
    resolve_working_dir,
)
from termux_agent.providers import create_provider
from termux_agent.tools.base import ToolContext
from termux_agent.ui.renderer import render_error, render_info
from termux_agent.ui.repl import Repl

READONLY_TOOLS = {
    "read_file",
    "list_dir",
    "grep_file",
    "glob_find",
    "web_fetch",
    "web_search",
    "git_status",
    "git_diff",
}


def build_agent(
    cfg: dict,
    provider_name: str | None,
    model: str | None,
    auto_accept: bool = False,
    agent_name: str | None = None,
    working_dir: str | None = None,
    temperature: float | None = None,
    max_tool_rounds: int | None = None,
    readonly: bool = False,
    max_context_tokens: int | None = None,
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
        raise ConfigError(f"Unknown agent '{agent_key}'. Available: {', '.join(cfg.get('agents', {}))}")
    if readonly:
        base = agent_spec.get("tools") or READONLY_TOOLS
        agent_spec = {
            "prompt": f"{agent_spec.get('prompt', '')}\nRead-only mode is active: DO NOT write or edit files, and DO NOT run any commands. Only read, search, and browse the web.",
            "tools": [t for t in base if t in READONLY_TOOLS],
        }
    return Agent(
        provider,
        ctx,
        max_tool_rounds=int(max_tool_rounds or cfg.get("max_tool_rounds", 20)),
        temperature=float(temperature if temperature is not None else cfg.get("temperature", 0.7)),
        agent_spec=agent_spec,
        max_context_tokens=int(max_context_tokens if max_context_tokens is not None else cfg.get("max_context_tokens", 0)),
    )


def cmd_init() -> int:
    if sys.stdin.isatty():
        return _init_wizard()
    path = ensure_config_file()
    render_info(f"Configuration created: {path}\n")
    render_info("Next steps:\n  1. Set the API key in an env var (e.g. export OPENAI_API_KEY=...)\n  2. Run: termux-agent")
    return 0


def _init_wizard() -> int:
    render_info("termux-agent setup (press Enter to keep the default)")
    providers = sorted(DEFAULTS.get("providers", {}))
    p = input(f"Provider [{'/'.join(providers)}] (default: zen) > ").strip() or "zen"
    pc = DEFAULTS.get("providers", {}).get(p)
    if pc is None:
        render_error(f"Unknown provider: {p}")
        return 1
    models = pc.get("models") or []
    default_model = models[0] if models else ""
    m = input(f"Model (default: {default_model or '(none)'}) > ").strip() or default_model
    cfg = copy.deepcopy(DEFAULTS)
    cfg["provider"] = p
    if m:
        cfg["model"] = m
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    render_info(f"Configuration created: {CONFIG_FILE}")
    key_env = pc.get("api_key_env")
    if key_env:
        render_info(
            f"API key: set the env var {key_env} (e.g. export {key_env}=...). "
            "Keys are never stored in the config file."
        )
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
    readonly: bool = False,
    plan: bool = False,
    as_json: bool = False,
    max_context_tokens: int | None = None,
) -> int:
    from termux_agent.ui.renderer import render_answer, render_tool_use

    agent = build_agent(cfg, provider, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, readonly, max_context_tokens)
    if plan and not readonly:
        return cmd_plan(cfg, prompt, provider, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, max_context_tokens)
    if not as_json:
        render_info(
            f"provider: {agent.provider.name} | model: {agent.provider.model} | "
            f"agent: {agent_name or cfg.get('agent', 'root')} | cwd: {agent.ctx.working_dir}"
        )
    if auto_accept and not as_json:
        render_info("Mode --yes: all confirmations are skipped automatically.")
    tool_log: list[dict] = []

    def _log_tool(name: str, args_str: str) -> None:
        tool_log.append({"name": name, "arguments": args_str})
        if not as_json:
            render_tool_use(name, args_str)

    try:
        answer = agent.run(prompt, on_tool_use=_log_tool)
    except KeyboardInterrupt:
        if as_json:
            _emit_json({"ok": False, "error": "cancelled"}, agent)
        else:
            render_error("\nCancelled.")
        return 130
    if as_json:
        _emit_json({"ok": True, "answer": answer, "tool_calls": tool_log}, agent)
    else:
        render_answer(answer)
    return 0


def _emit_json(payload: dict, agent: "Agent") -> None:
    import json

    payload["provider"] = agent.provider.name
    payload["model"] = agent.provider.model
    usage = getattr(agent, "usage", {})
    if usage:
        payload["usage"] = usage
    print(json.dumps(payload, ensure_ascii=False))


def cmd_plan(
    cfg: dict,
    prompt: str,
    provider: str | None,
    model: str | None,
    auto_accept: bool = False,
    agent_name: str | None = None,
    working_dir: str | None = None,
    temperature: float | None = None,
    max_tool_rounds: int | None = None,
    max_context_tokens: int | None = None,
) -> int:
    """Plan mode: first produce a plan read-only, then execute after approval."""
    from termux_agent.ui.renderer import render_answer, render_tool_use

    render_info("Planning mode: the agent will only propose a plan (no changes).")
    plan_agent = build_agent(
        cfg, provider, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, True, max_context_tokens
    )
    plan_prompt = (
        prompt
        + "\n\nFirst, analyze the request and produce a clear step-by-step plan. "
        "Do NOT modify anything yet - you are in planning mode."
    )
    try:
        plan = plan_agent.run(plan_prompt, on_tool_use=render_tool_use)
    except KeyboardInterrupt:
        render_error("\nCancelled.")
        return 130
    render_info("\n--- PLAN ---")
    render_answer(plan)
    render_info("--- END OF PLAN ---")

    proceed = auto_accept
    if not proceed and sys.stdin.isatty():
        try:
            proceed = input("\nExecute this plan? [y/N] > ").strip().lower() in ("y", "yes")
        except (KeyboardInterrupt, EOFError):
            proceed = False
    if not proceed:
        render_info("Plan not executed. Run again without --plan to let the agent act.")
        return 0

    exec_agent = build_agent(
        cfg, provider, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, False, max_context_tokens
    )
    exec_prompt = (
        f"Execute the approved plan below, then report the results.\n\n"
        f"APPROVED PLAN:\n{plan}\n\nORIGINAL REQUEST:\n{prompt}"
    )
    render_info("Executing plan...")
    try:
        answer = exec_agent.run(exec_prompt, on_tool_use=render_tool_use)
    except KeyboardInterrupt:
        render_error("\nCancelled.")
        return 130
    render_answer(answer)
    return 0


def cmd_sessions() -> int:
    from termux_agent.session import list_sessions, read_session

    sessions = list_sessions()
    if not sessions:
        render_info("No sessions saved yet in ~/.termux-agent/sessions/.")
        return 0
    for s in sessions[:20]:
        recs = read_session(s)
        first_user = next((r["content"] for r in recs if r.get("role") == "user"), "")
        render_info(f"{s.stem}  [{len(recs)} messages]  {first_user[:60]}")
    return 0


def find_session(session_ref: str | None) -> "tuple[Path, dict, list[dict]] | None":
    """Find a session file + provider info + message history."""
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
    readonly: bool = False,
    max_context_tokens: int | None = None,
) -> int:
    from termux_agent.ui.renderer import render_answer, render_tool_use

    found = find_session(session_ref)
    if not found:
        render_error("Session not found. Run 'termux-agent --sessions' to list them.")
        return 1
    path, info, history = found
    render_info(f"Resuming session {path.stem} ({len(history)} messages)")
    provider_name = info.get("provider") or cfg.get("provider", "zen")
    if provider_name not in cfg.get("providers", {}):
        provider_name = cfg.get("provider", "zen")
    model = info.get("model") or None
    agent = build_agent(cfg, provider_name, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, readonly, max_context_tokens)
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
        label = "all tools" if not tools else ", ".join(tools)
        render_info(f"{name:10} {spec.get('description', '')}  [{label}]")
    return 0


def cmd_list_models(cfg: dict, provider_name: str | None = None) -> int:
    name = provider_name or cfg.get("provider", "zen")
    try:
        provider = create_provider(name, cfg)
    except ConfigError as e:
        render_error(str(e))
        return 1
    live = provider.list_models()
    if live:
        render_info(f"Models for '{name}':")
        for m in live:
            render_info(f"  {m}")
        return 0
    render_info(f"'{name}' does not expose a live model list; showing presets:")
    for m in cfg.get("providers", {}).get(name, {}).get("models", []):
        render_info(f"  {m}")
    return 0


def cmd_doctor(cfg: dict, network: bool = False) -> int:
    """Environment diagnostics: versions, Termux, config, PATH, provider connectivity."""
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

    render_info("== environment ==")
    ok("python", f"{_sys.version.split()[0]} ({_sys.executable})")
    ok("platform", platform.platform())
    is_termux = "TERMUX_VERSION" in os.environ or os.path.isdir("/data/data/com.termux")
    if is_termux:
        ok("termux", "detected")
    else:
        warn("termux", "not detected - this might not be Termux")

    render_info("== configuration ==")
    ok("config", str(CONFIG_FILE))
    try:
        render_info(f"  provider: {cfg.get('provider')} | model: {cfg.get('model')} | agent: {cfg.get('agent')}")
    except Exception:  # noqa: BLE001
        pass
    try:
        cwd = resolve_working_dir(cfg)
        if os.access(cwd, os.W_OK):
            ok("working_dir writable", str(cwd))
        else:
            warn("working_dir", f"not writable: {cwd}")
    except Exception as e:  # noqa: BLE001
        warn("working_dir", str(e))
    if cfg.get("allow_storage"):
        from termux_agent.config import detect_storage_roots

        roots = detect_storage_roots()
        ok("storage roots", ", ".join(map(str, roots)) or "none")

    render_info("== PATH & tools ==")
    for name in ("git", "pip", "python"):
        path = shutil.which(name)
        ok(name, path or "NOT FOUND") if path else warn(name, "not found in PATH")
    ok("registered tools", str(n_tools))

    render_info("== provider ==")
    pname = cfg.get("provider", "zen")
    pc = cfg.get("providers", {}).get(pname, {})
    ok("active provider", f"{pname} ({pc.get('type')})")
    if pc.get("api_key_env"):
        key = os.environ.get(pc["api_key_env"])
        model = cfg.get("model", "")
        is_free = pname == "zen" and "free" in str(model)
        if key:
            ok("api key", f"{pc['api_key_env']} set ({key[:4]}...)")
        elif is_free:
            ok("api key", f"{pc['api_key_env']} empty - not required for zen free models")
        else:
            warn("api key", f"{pc['api_key_env']} empty - set the env var or fill it in the config")
    if network and pc.get("base_url"):
        import urllib.request

        try:
            req = urllib.request.Request(pc["base_url"], method="HEAD")
            urllib.request.urlopen(req, timeout=10).close()
            ok("connectivity", f"{pc['base_url']} reachable")
        except Exception as e:  # noqa: BLE001
            warn("connectivity", f"{pc['base_url']}: {type(e).__name__}")

    from termux_agent.session import list_sessions

    ok("saved sessions", str(len(list_sessions())))
    render_info("\nIf you see [!!] markers, rerun with TERMUX_AGENT_DEBUG=1 for detailed logs.")
    return 1 if issues else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="termux-agent",
        description="A CLI coding agent for Termux, like opencode.",
    )
    parser.add_argument("--version", action="version", version=f"termux-agent {__version__}")
    parser.add_argument("--provider", help="Provider name (e.g. openai, anthropic, ollama)")
    parser.add_argument("--model", help="Model name (overrides the config default)")
    parser.add_argument("--agent", help="Sub-agent name (e.g. explore, coder, shell)")
    parser.add_argument("--cwd", help="Working directory (overrides config working_dir)")
    parser.add_argument("--temperature", type=float, help="Sampling temperature (0.0-2.0)")
    parser.add_argument("--max-tool-rounds", type=int, help="Max tool iterations per message")
    parser.add_argument("--max-context-tokens", type=int, help="Auto-compact history when cumulative tokens pass this budget (0=off)")
    parser.add_argument("--verbose", action="store_true", help="Log provider requests/responses (same as TERMUX_AGENT_DEBUG=1)")
    parser.add_argument("--readonly", action="store_true", help="Read-only mode: cannot write/edit/run commands")
    parser.add_argument("--plan", action="store_true", help="Plan mode: propose a plan first, execute only after approval")
    parser.add_argument("--json", action="store_true", help="One-shot mode: print the result as JSON (answer, tool_calls, usage)")
    parser.add_argument("prompt", nargs="*", help="One-shot prompt (no arguments = interactive mode)")
    parser.add_argument("--init", action="store_true", help="Create config.example -> ~/.termux-agent/config.yaml")
    parser.add_argument("--sessions", action="store_true", help="List saved sessions")
    parser.add_argument("--list-providers", action="store_true", help="List provider presets")
    parser.add_argument("--list-agents", action="store_true", help="List available sub-agents")
    parser.add_argument("--models", nargs="?", const="__default__", metavar="PROVIDER", help="List models for a provider (live, or preset fallback)")
    parser.add_argument("--doctor", action="store_true", help="Diagnose environment & config")
    parser.add_argument("--doctor-network", action="store_true", help="Also check provider connectivity (needs internet)")
    parser.add_argument(
        "--install-completion",
        nargs="?",
        const="bash",
        metavar="SHELL",
        help="Install auto-completion into .bashrc/.zshrc (default bash)",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        metavar="ID",
        help="Resume a previous session (default: latest)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip all confirmations (dangerous: allows any command & commit)",
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

    # Auto-create ~/.termux-agent/config.yaml on first run (like opencode).
    if not CONFIG_FILE.exists():
        ensure_config_file()
        render_info(
            f"First-run configuration created at {CONFIG_FILE} - edit it if needed, "
            "or just start using it (default: free OpenCode Zen)."
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
    if args.models is not None:
        pname = None if args.models == "__default__" else args.models
        return cmd_list_models(cfg, pname)
    if args.install_completion:
        from termux_agent.completion import install

        shell = args.install_completion.lower()
        try:
            rc = install(shell)
            render_info(f"Auto-completion {shell} installed in {rc}. Open a new terminal or run 'source {rc}'.")
        except ValueError as e:
            render_error(f"Error: {e}")
            return 1
        return 0

    prompt = " ".join(args.prompt).strip()
    if not prompt and not sys.stdin.isatty():
        # Pipelines: read the whole stdin as a one-shot prompt.
        prompt = sys.stdin.read().strip()
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
            readonly=args.readonly,
            max_context_tokens=args.max_context_tokens,
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
            readonly=args.readonly,
            plan=args.plan,
            as_json=args.json,
            max_context_tokens=args.max_context_tokens,
        )

    if args.json:
        render_error("--json requires a one-shot prompt (e.g. termux-agent --json 'summarize this repo').")
        return 2

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
            readonly=args.readonly,
            max_context_tokens=args.max_context_tokens,
        )
    except (ConfigError, KeyError) as e:
        render_error(f"Error: {e}\nRun 'termux-agent --init' first, then set the API key.")
        return 1
    if args.yes:
        render_info("Mode --yes: all confirmations are skipped automatically.")
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