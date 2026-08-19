"""CLI entry point: interactive mode, one-shot, init-config, sessions."""
from __future__ import annotations

import argparse
import copy
import os
import sys
import threading
from pathlib import Path

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
    no_tools: bool = False,
    retries: int | None = None,
    no_fallback: bool = False,
) -> Agent:
    name = provider_name or cfg.get("provider", "zen")
    provider = create_provider(name, cfg, model)
    if no_fallback:
        provider.fallback_models = []
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
        whitelisted_commands=[str(c) for c in (cfg.get("whitelisted_commands") or [])],
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
        retries=int(retries if retries is not None else cfg.get("retries", 1)),
        retry_backoff=float(cfg.get("retry_backoff", 1.0)),
    )._with_tools(not no_tools)


def cmd_init(provider: str | None = None, model: str | None = None) -> int:
    if provider or model:
        return _init_noninteractive(provider, model)
    if sys.stdin.isatty():
        return _init_wizard()
    path = ensure_config_file()
    render_info(f"Configuration created: {path}\n")
    render_info("Next steps:\n  1. Set the API key in an env var (e.g. export OPENAI_API_KEY=...)\n  2. Run: termux-agent")
    return 0


def _init_noninteractive(provider: str | None, model: str | None) -> int:
    """Create ~/.termux-agent/config.yaml with the chosen provider/model (no wizard)."""
    cfg = copy.deepcopy(DEFAULTS)
    if provider:
        if provider not in cfg.get("providers", {}):
            render_error(f"Unknown provider: {provider}")
            return 1
        cfg["provider"] = provider
    pname = cfg.get("provider", "zen")
    if model:
        cfg["providers"][pname]["models"] = [model]
        cfg["model"] = model
    import yaml as _yaml

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(_yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    render_info(f"Configuration created: {CONFIG_FILE}")
    render_info(f"Provider: {pname} | Model: {model or cfg['providers'][pname]['models'][0]}")
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
    try:
        run_smoke = input("\nRun a quick smoke test now? [y/N] > ").strip().lower() in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        run_smoke = False
    if run_smoke:
        return cmd_smoke(cfg, p, m or None)
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
    quiet: bool = False,
    copy: bool = False,
    stats: bool = False,
    no_tools: bool = False,
    wakelock: bool = False,
    speak: bool = False,
    timeout: int | None = None,
    output: str | None = None,
    clip: bool = False,
    screenshot: bool = False,
    stream: bool = False,
    retries: int | None = None,
    no_fallback: bool = False,
) -> int:
    from termux_agent.ui.renderer import render_answer, render_tool_use

    if clip and not prompt:
        from termux_agent.notify import clipboard_get

        prompt = clipboard_get() or prompt
        if not prompt:
            render_error("Clipboard is empty (or termux-api is not installed).")
            return 2
        render_info("Using clipboard as prompt.")
    if screenshot:
        from termux_agent.notify import screenshot as _screenshot

        img = _screenshot()
        if not img:
            render_error("Could not take a screenshot (is termux-api installed and screen sharing granted?).")
            return 2
        prompt = f"{prompt}\n\n[image: {img}]".strip() if prompt else f"Describe this screenshot:\n\n[image: {img}]"
        render_info(f"Attached screenshot: {img}")

    agent = build_agent(cfg, provider, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, readonly, max_context_tokens, no_tools, retries, no_fallback)
    if plan and not readonly:
        return cmd_plan(cfg, prompt, provider, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, max_context_tokens, as_json)
    if not as_json and not quiet:
        render_info(
            f"provider: {agent.provider.name} | model: {agent.provider.model} | "
            f"agent: {agent_name or cfg.get('agent', 'root')} | cwd: {agent.ctx.working_dir}"
        )
    if auto_accept and not as_json and not quiet:
        render_info("Mode --yes: all confirmations are skipped automatically.")
    tool_log: list[dict] = []

    def _log_tool(name: str, args_str: str) -> None:
        tool_log.append({"name": name, "arguments": args_str})
        if not as_json and not quiet:
            render_tool_use(name, args_str)

    if wakelock:
        from termux_agent.notify import wake_lock

        wake_lock()
    streamed = stream and not as_json and not quiet
    try:
        if streamed:
            from termux_agent.ui.renderer import PlainStreamPrinter

            printer = PlainStreamPrinter()
            answer = _run_guarded(agent, prompt, _log_tool, timeout, on_text_delta=printer.feed)
            printer.flush()
        else:
            answer = _run_guarded(agent, prompt, _log_tool, timeout)
    except KeyboardInterrupt:
        if wakelock:
            from termux_agent.notify import wake_unlock

            wake_unlock()
        if as_json:
            _emit_json({"ok": False, "error": "cancelled"}, agent)
        else:
            render_error("\nCancelled.")
        return 130
    except TimeoutError:
        if wakelock:
            from termux_agent.notify import wake_unlock

            wake_unlock()
        if as_json:
            _emit_json({"ok": False, "error": f"timed out after {timeout}s"}, agent)
        else:
            render_error(f"\nTimed out after {timeout}s.")
        return 124
    if wakelock:
        from termux_agent.notify import wake_unlock

        wake_unlock()
    if speak:
        from termux_agent.notify import speak as _speak

        _speak(answer)
    if output:
        try:
            Path(output).write_text(answer + "\n", encoding="utf-8")
        except OSError as e:
            render_error(f"Cannot write output file {output}: {e}")
    if getattr(agent, "messages", None):
        from termux_agent.session import record_messages

        record_messages(agent.messages, agent.provider.name, agent.provider.model)
    _maybe_notify(cfg, "Done", answer, as_json)
    if copy:
        from termux_agent.ui.repl import copy_to_clipboard

        if copy_to_clipboard(answer):
            if not quiet:
                render_info("Answer copied to the clipboard.")
        elif not quiet:
            render_error("Clipboard unavailable. Install termux-api (pkg install termux-api).")
    if as_json:
        _emit_json({"ok": True, "answer": answer, "tool_calls": tool_log}, agent)
    elif quiet:
        print(answer)
    elif not streamed:
        render_answer(answer)
    if stats and not as_json:
        u = agent.usage
        if u and any(u.values()):
            render_info(
                f"Tokens: prompt {u.get('prompt_tokens', 0)} | completion {u.get('completion_tokens', 0)} | total {u.get('total_tokens', 0)}"
            )
    return 0


def _emit_json(payload: dict, agent: "Agent | None") -> None:
    import json

    if agent is not None:
        payload["provider"] = agent.provider.name
        payload["model"] = agent.provider.model
        usage = getattr(agent, "usage", {})
        if usage:
            payload["usage"] = usage
    print(json.dumps(payload, ensure_ascii=False))


def _maybe_notify(cfg: dict, title: str, answer: str, as_json: bool = False) -> None:
    if not (cfg.get("notify_on_done") or os.environ.get("TERMUX_AGENT_NOTIFY") == "1"):
        return
    from termux_agent.notify import notify

    preview = " ".join(answer.split())[:120] or "(empty answer)"
    notify(f"{title}: {preview}")


def _run_guarded(agent: Agent, prompt: str, on_tool_use, timeout: int | None, on_text_delta=None):
    """Run the agent, aborting with TimeoutError after `timeout` seconds (0 = unlimited)."""
    if on_text_delta is None:
        if not timeout:
            return agent.run(prompt, on_tool_use=on_tool_use)
        result: dict = {}

        def _worker() -> None:
            result["answer"] = agent.run(prompt, on_tool_use=on_tool_use)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise TimeoutError
        return result["answer"]
    if not timeout:
        return agent.run(prompt, on_tool_use=on_tool_use, on_text_delta=on_text_delta)
    result: dict = {}

    def _worker() -> None:
        result["answer"] = agent.run(prompt, on_tool_use=on_tool_use, on_text_delta=on_text_delta)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError
    return result["answer"]


def cmd_bench(cfg: dict, provider_name: str | None = None, timeout: int = 60) -> int:
    """Time one tiny prompt against each model of a provider (best-effort)."""
    import time

    from termux_agent.ui.renderer import render_error, render_info

    provider_name = provider_name or cfg.get("provider", "zen")
    models = (cfg.get("providers", {}).get(provider_name, {}).get("models") or [])
    if not models:
        render_error(f"Provider '{provider_name}' has no preset models to benchmark.")
        return 1
    render_info(f"Benchmarking {provider_name}: {len(models)} model(s) — one tiny request each.")
    results: list[tuple[str, float, int, bool]] = []
    for m in models:
        start = time.monotonic()
        try:
            answer = _run_guarded(build_agent(cfg, provider_name, m, auto_accept=True), "Reply with exactly: ok", None, timeout)
            dt = time.monotonic() - start
            results.append((m, dt, len(answer), True))
        except Exception:  # noqa: BLE001
            results.append((m, time.monotonic() - start, 0, False))
    from rich.table import Table

    from termux_agent.ui.renderer import console

    table = Table(title=f"Latency benchmark: {provider_name}", expand=False)
    table.add_column("model")
    table.add_column("time (s)", justify="right")
    table.add_column("chars", justify="right")
    table.add_column("status")
    for m, dt, chars, ok in sorted(results, key=lambda r: r[1]):
        table.add_row(m, f"{dt:.1f}", str(chars), "ok" if ok else "FAILED")
    console.print(table)
    return 0


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
    as_json: bool = False,
) -> int:
    """Plan mode: first produce a plan read-only, then execute after approval."""
    from termux_agent.ui.renderer import render_answer, render_tool_use

    if not as_json:
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
        if as_json:
            _emit_json({"ok": False, "error": "cancelled"}, plan_agent)
        else:
            render_error("\nCancelled.")
        return 130
    if not as_json:
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
        if as_json:
            _emit_json({"ok": True, "plan": plan, "executed": False}, plan_agent)
        else:
            render_info("Plan not executed. Run again without --plan to let the agent act.")
        return 0

    exec_agent = build_agent(
        cfg, provider, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, False, max_context_tokens
    )
    exec_prompt = (
        f"Execute the approved plan below, then report the results.\n\n"
        f"APPROVED PLAN:\n{plan}\n\nORIGINAL REQUEST:\n{prompt}"
    )
    if not as_json:
        render_info("Executing plan...")
    try:
        answer = exec_agent.run(exec_prompt, on_tool_use=render_tool_use)
    except KeyboardInterrupt:
        if as_json:
            _emit_json({"ok": False, "error": "cancelled", "plan": plan}, exec_agent)
        else:
            render_error("\nCancelled.")
        return 130
    if as_json:
        _emit_json({"ok": True, "plan": plan, "executed": True, "answer": answer}, exec_agent)
    else:
        render_answer(answer)
    return 0


def cmd_export(ref: str | None = None) -> int:
    """Print a session as portable JSON (default: latest)."""
    from termux_agent.session import export_session

    try:
        data = export_session(ref)
    except FileNotFoundError:
        render_error("Session not found.")
        return 1
    import json as _json

    print(_json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_import(path: str) -> int:
    """Import a portable session JSON file and save it as a session."""
    import json as _json

    from termux_agent.session import import_session

    try:
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        sid = import_session(data)
    except FileNotFoundError:
        render_error(f"File not found: {path}")
        return 1
    except (ValueError, _json.JSONDecodeError) as e:
        render_error(f"Invalid session file: {e}")
        return 1
    render_info(f"Imported session {sid} ({len(data.get('messages', []))} messages)")
    return 0


def cmd_prune(keep: int) -> int:
    from termux_agent.session import prune_sessions

    removed = prune_sessions(max(0, keep))
    render_info(f"Removed {removed} old session(s), keeping the newest {max(0, keep)}.")
    return 0


def cmd_forget(ref: str | None = None) -> int:
    from termux_agent.session import delete_session

    removed = delete_session(ref)
    if not removed:
        render_error("Session not found.")
        return 1
    render_info(f"Deleted session {removed.stem}.")
    return 0


def cmd_export_all(target_dir: str) -> int:
    import json as _json

    from termux_agent.session import export_session, list_sessions

    out = Path(target_dir)
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for s in list_sessions():
        data = export_session(s.stem)
        (out / f"{s.stem}.json").write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        count += 1
    render_info(f"Exported {count} session(s) to {out}.")
    return 0


def cmd_sessions(search: str | None = None) -> int:
    from termux_agent.session import list_sessions, read_session

    sessions = list_sessions()
    if not sessions:
        render_info("No sessions saved yet in ~/.termux-agent/sessions/.")
        return 0
    shown = 0
    needle = search.lower() if search else ""
    for s in sessions[:200]:
        recs = read_session(s)
        first_user = next((r["content"] for r in recs if r.get("role") == "user"), "")
        if needle and needle not in first_user.lower():
            continue
        render_info(f"{s.stem}  [{len(recs)} messages]  {first_user[:60]}")
        shown += 1
        if shown >= 20:
            break
    if needle:
        render_info(f"\n{shown} matching session(s).")
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
    as_json: bool = False,
    quiet: bool = False,
) -> int:
    from termux_agent.ui.renderer import render_answer, render_tool_use

    found = find_session(session_ref)
    if not found:
        render_error("Session not found. Run 'termux-agent --sessions' to list them.")
        return 1
    path, info, history = found
    if not quiet and not as_json:
        render_info(f"Resuming session {path.stem} ({len(history)} messages)")
    provider_name = info.get("provider") or cfg.get("provider", "zen")
    if provider_name not in cfg.get("providers", {}):
        provider_name = cfg.get("provider", "zen")
    model = info.get("model") or None
    agent = build_agent(cfg, provider_name, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, readonly, max_context_tokens)
    agent.messages = [{"role": "system", "content": agent.system_prompt}] + history
    if prompt:
        def _log(name: str, args_str: str) -> None:
            if not as_json and not quiet:
                render_tool_use(name, args_str)

        answer = agent.run(prompt, on_tool_use=_log)
        _maybe_notify(cfg, "Resume done", answer, as_json)
        if as_json:
            _emit_json({"ok": True, "answer": answer, "session": path.stem}, agent)
        elif quiet:
            print(answer)
        else:
            render_answer(answer)
        return 0
    if as_json or quiet:
        render_error("--json/--quiet require a prompt with --resume.")
        return 2
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


def cmd_doctor(cfg: dict, network: bool = False, as_json: bool = False) -> int:
    """Environment diagnostics: versions, Termux, config, PATH, provider connectivity."""
    import os
    import platform
    import shutil
    import sys as _sys

    checks: list[dict] = []

    def add(label: str, ok_flag: bool, detail: str = "") -> None:
        checks.append({"label": label, "ok": ok_flag, "detail": detail})

    try:
        import termux_agent.tools.base as tbase

        n_tools = len(tbase.tool_specs())
    except Exception:  # noqa: BLE001
        n_tools = 0

    add("python", True, f"{_sys.version.split()[0]} ({_sys.executable})")
    add("platform", True, platform.platform())
    is_termux = "TERMUX_VERSION" in os.environ or os.path.isdir("/data/data/com.termux")
    add("termux", is_termux, "detected" if is_termux else "not detected - this might not be Termux")
    add("config", True, str(CONFIG_FILE))
    add("provider", True, f"{cfg.get('provider')} / model: {cfg.get('model')} / agent: {cfg.get('agent')}")
    try:
        cwd = resolve_working_dir(cfg)
        add("working_dir writable", os.access(cwd, os.W_OK), str(cwd))
    except Exception as e:  # noqa: BLE001
        add("working_dir", False, str(e))
    if cfg.get("allow_storage"):
        from termux_agent.config import detect_storage_roots

        roots = detect_storage_roots()
        add("storage roots", True, ", ".join(map(str, roots)) or "none")
    for name in ("git", "pip", "python"):
        path = shutil.which(name)
        add(name, bool(path), path or "not found in PATH")
    add("registered tools", True, str(n_tools))
    pname = cfg.get("provider", "zen")
    pc = cfg.get("providers", {}).get(pname, {})
    add("active provider", True, f"{pname} ({pc.get('type')})")
    if pc.get("api_key_env"):
        key = os.environ.get(pc["api_key_env"])
        model = cfg.get("model", "")
        is_free = pname == "zen" and "free" in str(model)
        if key:
            add("api key", True, f"{pc['api_key_env']} set ({key[:4]}...)")
        elif is_free:
            add("api key", True, f"{pc['api_key_env']} empty - not required for zen free models")
        else:
            add("api key", False, f"{pc['api_key_env']} empty - set the env var or fill it in the config")
    if network and pc.get("base_url"):
        import urllib.request

        try:
            req = urllib.request.Request(pc["base_url"], method="HEAD")
            urllib.request.urlopen(req, timeout=10).close()
            add("connectivity", True, f"{pc['base_url']} reachable")
        except Exception as e:  # noqa: BLE001
            add("connectivity", False, f"{pc['base_url']}: {type(e).__name__}")

    from termux_agent.session import list_sessions

    add("saved sessions", True, str(len(list_sessions())))

    if as_json:
        import json as _json

        _emit_json({"ok": all(c["ok"] for c in checks), "checks": checks}, None)
        return 0 if all(c["ok"] for c in checks) else 1

    issues = sum(1 for c in checks if not c["ok"])
    render_info("== environment ==")
    for c in checks[:3]:
        (render_info if c["ok"] else render_error)(f"  [{'OK' if c['ok'] else '!!'}]  {c['label']}" + (f": {c['detail']}" if c["detail"] else ""))
    render_info("== configuration ==")
    for c in checks[3:6]:
        (render_info if c["ok"] else render_error)(f"  [{'OK' if c['ok'] else '!!'}]  {c['label']}" + (f": {c['detail']}" if c["detail"] else ""))
    if cfg.get("allow_storage"):
        for c in checks[6:7]:
            (render_info if c["ok"] else render_error)(f"  [{'OK' if c['ok'] else '!!'}]  {c['label']}" + (f": {c['detail']}" if c["detail"] else ""))
    render_info("== PATH & tools ==")
    for c in checks[7:11]:
        (render_info if c["ok"] else render_error)(f"  [{'OK' if c['ok'] else '!!'}]  {c['label']}" + (f": {c['detail']}" if c["detail"] else ""))
    render_info("== provider ==")
    for c in checks[11:]:
        (render_info if c["ok"] else render_error)(f"  [{'OK' if c['ok'] else '!!'}]  {c['label']}" + (f": {c['detail']}" if c["detail"] else ""))
    render_info("\nIf you see [!!] markers, rerun with TERMUX_AGENT_DEBUG=1 for detailed logs.")
    return 1 if issues else 0


def cmd_smoke(cfg: dict, provider: str | None, model: str | None) -> int:
    """End-to-end smoke test: send a tiny prompt and verify the whole pipeline."""
    import time

    from termux_agent.ui.renderer import render_tool_use

    try:
        agent = build_agent(cfg, provider, model, auto_accept=True)
    except (ConfigError, KeyError) as e:
        render_error(f"Error: {e}")
        return 1
    render_info(
        f"Smoke test: provider={agent.provider.name} model={agent.provider.model} cwd={agent.ctx.working_dir}"
    )
    start = time.monotonic()
    try:
        answer = agent.run("Reply with exactly: OK", on_tool_use=render_tool_use)
    except Exception as e:  # noqa: BLE001
        render_error(f"Smoke test FAILED: {type(e).__name__}: {e}")
        return 1
    elapsed = time.monotonic() - start
    usage = agent.usage
    ok = bool(answer.strip())
    _maybe_notify(cfg, "Smoke test " + ("OK" if ok else "FAILED"), answer)
    render_info(
        f"Done in {elapsed:.1f}s | tokens: prompt {usage.get('prompt_tokens', 0)} / "
        f"completion {usage.get('completion_tokens', 0)} | answer: {answer!r}"
    )
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
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
    parser.add_argument("--quiet", action="store_true", help="One-shot mode: print only the answer (no banner/tool logs)")
    parser.add_argument("--copy", action="store_true", help="One-shot mode: copy the answer to the clipboard")
    parser.add_argument("--image", help="Attach an image to the one-shot prompt (vision-capable models, e.g. a screenshot)")
    parser.add_argument("--prompt-file", help="Read the prompt from a file (UTF-8)")
    parser.add_argument("--api-key", help="Set the provider API key for this run (env var override, not saved)")
    parser.add_argument("--stats", action="store_true", help="One-shot mode: print token usage after the answer")
    parser.add_argument("--chat", action="store_true", help="Chat mode: disable all tools (plain conversation, no file/command access)")
    parser.add_argument("--notify", action="store_true", help="Send a Termux notification when a one-shot task finishes (needs termux-api)")
    parser.add_argument("--wakelock", action="store_true", help="Hold a Termux wake lock while a one-shot task runs (needs termux-api)")
    parser.add_argument("--speak", action="store_true", help="Read the answer aloud with termux-tts-speak (needs termux-api)")
    parser.add_argument("--timeout", type=int, metavar="SECONDS", help="Abort a one-shot task if it takes longer than this")
    parser.add_argument("--output", metavar="FILE", help="Also write the answer to this file (plain text)")
    parser.add_argument("--clip", action="store_true", help="Use the clipboard as the prompt (needs termux-api)")
    parser.add_argument("--screenshot", action="store_true", help="Attach a screenshot of the screen to the prompt (needs termux-api + screen share)")
    parser.add_argument("--stream", action="store_true", help="Stream the answer to the terminal as it is generated (typewriter mode)")
    parser.add_argument("--retries", type=int, metavar="N", help="Override transient retry count for network hiccups")
    parser.add_argument("--no-fallback", action="store_true", help="Disable fallback models on 429/errors (use only the selected model)")
    parser.add_argument("--serve", action="store_true", help="Run a tiny HTTP API server (POST /chat, GET /health, GET /models)")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP server bind host (with --serve)")
    parser.add_argument("--port", type=int, default=8787, help="HTTP server port (with --serve)")
    parser.add_argument("--token", help="Require this bearer token for the HTTP API (with --serve; use a long random string)")
    parser.add_argument("prompt", nargs="*", help="One-shot prompt (no arguments = interactive mode)")
    parser.add_argument("--init", action="store_true", help="Create config.example -> ~/.termux-agent/config.yaml")
    parser.add_argument("--sessions", action="store_true", help="List saved sessions")
    parser.add_argument("--search", help="Filter --sessions by keyword in the first message")
    parser.add_argument("--export", nargs="?", const="latest", metavar="SESSION", help="Print a session as portable JSON (default: latest)")
    parser.add_argument("--import", dest="import_path", metavar="FILE", help="Import a portable session JSON file")
    parser.add_argument("--prune", type=int, metavar="N", help="Delete all sessions except the newest N")
    parser.add_argument("--forget", nargs="?", const="latest", metavar="SESSION", help="Delete one session (default: latest)")
    parser.add_argument("--export-all", metavar="DIR", help="Export every session as a JSON file into DIR")
    parser.add_argument("--bench", nargs="?", const="__default__", metavar="PROVIDER", help="Benchmark latency across a provider's models (one tiny request each)")
    parser.add_argument("--list-providers", action="store_true", help="List provider presets")
    parser.add_argument("--list-agents", action="store_true", help="List available sub-agents")
    parser.add_argument("--models", nargs="?", const="__default__", metavar="PROVIDER", help="List models for a provider (live, or preset fallback)")
    parser.add_argument("--doctor", action="store_true", help="Diagnose environment & config")
    parser.add_argument("--doctor-network", action="store_true", help="Also check provider connectivity (needs internet)")
    parser.add_argument("--config", metavar="FILE", help="Use this config file instead of ~/.termux-agent/config.yaml")
    parser.add_argument("--smoke", action="store_true", help="End-to-end smoke test with the real model (sends a tiny prompt)")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.init:
        return cmd_init(args.provider, args.model)
    if args.bench:
        return cmd_bench(cfg, args.bench, args.timeout or 60)
    if args.export:
        return cmd_export(args.export)
    if args.export_all:
        return cmd_export_all(args.export_all)
    if args.forget:
        return cmd_forget(args.forget)
    if args.import_path:
        return cmd_import(args.import_path)
    if args.prune is not None:
        return cmd_prune(args.prune)
    if args.sessions:
        return cmd_sessions(args.search)

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        render_error(str(e))
        return 1

    # Auto-create ~/.termux-agent/config.yaml on first run (like opencode).
    if not CONFIG_FILE.exists() and not args.config:
        ensure_config_file()
        render_info(
            f"First-run configuration created at {CONFIG_FILE} - edit it if needed, "
            "or just start using it (default: free OpenCode Zen)."
        )

    if args.doctor or args.doctor_network:
        return cmd_doctor(cfg, network=args.doctor_network, as_json=args.json)
    if args.smoke:
        return cmd_smoke(cfg, args.provider, args.model)
    if args.serve:
        from termux_agent.server import serve

        return serve(cfg, host=args.host, port=args.port, provider=args.provider, model=args.model, auto_accept=args.yes, token=args.token)

    if args.verbose:
        import os

        os.environ["TERMUX_AGENT_DEBUG"] = "1"

    if args.api_key:
        import os

        pname = args.provider or cfg.get("provider", "zen")
        pc = cfg.get("providers", {}).get(pname, {})
        env_name = pc.get("api_key_env", "")
        if not env_name:
            render_error(f"Provider '{pname}' has no api_key_env to set.")
            return 1
        os.environ[env_name] = args.api_key
        if pname != "zen":
            render_info(f"API key set for {pname} via --api-key (not saved).")
    if args.notify:
        from termux_agent.notify import notify_on_done

        notify_on_done(True)

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
    if args.prompt_file != "-" and not prompt and not sys.stdin.isatty():
        # Pipelines: read the whole stdin as a one-shot prompt.
        prompt = sys.stdin.read().strip()
    if args.prompt_file:
        from pathlib import Path as _Path

        try:
            if args.prompt_file == "-":
                file_prompt = sys.stdin.read().strip()
            else:
                file_prompt = _Path(args.prompt_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            render_error(f"Cannot read --prompt-file: {e}")
            return 1
        prompt = (prompt + "\n\n" + file_prompt).strip() if prompt else file_prompt
    if args.image:
        if not prompt:
            render_error("--image requires a one-shot prompt (or --prompt-file).")
            return 2
        img = args.image
        if not Path(img).expanduser().is_file():
            render_error(f"Image not found: {img}")
            return 1
        prompt = f"{prompt}\n[image: {img}]"
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
            as_json=args.json,
            quiet=args.quiet,
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
            quiet=args.quiet,
            copy=args.copy,
            stats=args.stats,
            no_tools=args.chat,
            wakelock=args.wakelock,
            speak=args.speak,
            timeout=args.timeout,
            output=args.output,
            clip=args.clip,
            screenshot=args.screenshot,
            stream=args.stream,
            retries=args.retries,
            no_fallback=args.no_fallback,
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
            no_tools=args.chat,
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