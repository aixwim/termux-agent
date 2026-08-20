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
    extra_rules: str | None = None,
    system_prompt: str | None = None,
    disabled_groups: list[str] | None = None,
    max_output_chars: int | None = None,
    command_timeout: int | None = None,
    only_tools: list[str] | None = None,
    memory: bool = True,
    allow_dirs: list[str] | None = None,
) -> Agent:
    from pathlib import Path

    name = provider_name or cfg.get("provider", "zen")
    provider = create_provider(name, cfg, model)
    if no_fallback:
        provider.fallback_models = []
    if working_dir:
        cwd = Path(working_dir).expanduser().resolve()
        cwd.mkdir(parents=True, exist_ok=True)
    else:
        cwd = resolve_working_dir(cfg)
    ctx = ToolContext(
        working_dir=cwd,
        max_output_chars=int(max_output_chars if max_output_chars is not None else cfg.get("max_output_chars", 60000)),
        command_timeout=int(command_timeout if command_timeout is not None else cfg.get("command_timeout", 60)),
        confirm_commands=not auto_accept and bool(cfg.get("confirm_commands", True)),
        whitelisted_commands=[str(c) for c in (cfg.get("whitelisted_commands") or [])],
    )
    if cfg.get("allow_storage"):
        from termux_agent.config import detect_storage_roots

        ctx._allowed_dirs = [*ctx._allowed_dirs, *detect_storage_roots()]
    if allow_dirs:
        ctx._allowed_dirs = [
            *ctx._allowed_dirs,
            *[str(Path(d).expanduser().resolve()) for d in allow_dirs if d],
        ]
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
    agent = (
        Agent(
            provider,
            ctx,
            max_tool_rounds=int(max_tool_rounds or cfg.get("max_tool_rounds", 20)),
            temperature=float(temperature if temperature is not None else cfg.get("temperature", 0.7)),
            system_prompt=system_prompt,
            agent_spec=agent_spec,
            max_context_tokens=int(max_context_tokens if max_context_tokens is not None else cfg.get("max_context_tokens", 0)),
            retries=int(retries if retries is not None else cfg.get("retries", 1)),
            retry_backoff=float(cfg.get("retry_backoff", 1.0)),
            memory=memory,
        )
        ._with_tools(not no_tools)
        ._with_extra_rules(extra_rules)
        ._without_groups(disabled_groups or [])
    )
    if only_tools:
        agent._only_tools(only_tools)
    return agent


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
    rules_file: str | None = None,
    system_prompt_file: str | None = None,
    context: bool = False,
    disabled_groups: list[str] | None = None,
    max_output_chars: int | None = None,
    command_timeout: int | None = None,
    no_save: bool = False,
    git_context: bool = False,
    only_tools: list[str] | None = None,
    log_file: str | None = None,
    memory: bool = True,
    allow_dirs: list[str] | None = None,
    screenshot_dir: str | None = None,
    attach: list[str] | None = None,
    rotate: bool = False,
) -> int:
    from termux_agent.ui.renderer import render_answer, render_tool_use

    if attach:
        for f in attach:
            try:
                content = Path(f).expanduser().read_text(encoding="utf-8")
            except OSError as e:
                render_error(f"Cannot read --attach file: {e}")
                return 1
            prompt = f"{prompt}\n\n<file name={f}>\n{content}\n</file>".strip()
        render_info(f"Attached {len(attach)} file(s) to the prompt.")

    if clip and not prompt:
        from termux_agent.notify import clipboard_get

        prompt = clipboard_get() or prompt
        if not prompt:
            render_error("Clipboard is empty (or termux-api is not installed).")
            return 2
        render_info("Using clipboard as prompt.")
    if screenshot:
        from termux_agent.notify import screenshot as _screenshot

        shot_dir = Path(screenshot_dir).expanduser() if screenshot_dir else None
        if shot_dir:
            shot_dir.mkdir(parents=True, exist_ok=True)
            img = _screenshot(str(shot_dir / f"screenshot-{int(__import__('time').time())}.png"))
        else:
            img = _screenshot()
        if not img:
            render_error("Could not take a screenshot (is termux-api installed and screen sharing granted?).")
            return 2
        prompt = f"{prompt}\n\n[image: {img}]".strip() if prompt else f"Describe this screenshot:\n\n[image: {img}]"
        render_info(f"Attached screenshot: {img}")

    extra_rules = ""
    if rules_file:
        try:
            extra_rules = Path(rules_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            render_error(f"Cannot read --rules file: {e}")
            return 1
        if not extra_rules:
            render_error(f"--rules file is empty: {rules_file}")
            return 1
    if git_context:
        cwd = Path(working_dir).expanduser().resolve() if working_dir else resolve_working_dir(cfg)
        git_text = _git_context(cwd)
        if git_text:
            extra_rules = (extra_rules + "\n\n" + git_text) if extra_rules else git_text

    sys_prompt = None
    if system_prompt_file:
        try:
            sys_prompt = Path(system_prompt_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            render_error(f"Cannot read --system-prompt file: {e}")
            return 1
        if not sys_prompt:
            render_error(f"--system-prompt file is empty: {system_prompt_file}")
            return 1

    def _make_agent(which_model: str | None = None) -> Agent:
        a = build_agent(cfg, provider, which_model if which_model else model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, readonly, max_context_tokens, no_tools, retries, no_fallback, extra_rules, sys_prompt, disabled_groups, max_output_chars, command_timeout, only_tools=only_tools, memory=memory, allow_dirs=allow_dirs)
        if context:
            from termux_agent.notify import device_context

            _attach_agent_context(a, device_context())
        return a

    models_to_try = [model]
    if rotate and not model:
        pmodels = cfg.get("providers", {}).get(provider or cfg.get("provider", "zen"), {}).get("models") or []
        if pmodels:
            models_to_try = pmodels

    agent = _make_agent(models_to_try[0] if (rotate and not model) else model)
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
    logger = _run_logger(log_file) if log_file else None

    def _log_tool(name: str, args_str: str) -> None:
        tool_log.append({"name": name, "arguments": args_str})
        if logger:
            logger("tool", {"name": name, "arguments": args_str})
        if not as_json and not quiet:
            render_tool_use(name, args_str)

    if wakelock:
        from termux_agent.notify import wake_lock

        wake_lock()
    streamed = stream and not as_json and not quiet
    try:
        for idx, m in enumerate(models_to_try):
            if idx > 0:
                agent = _make_agent(m)
                if not as_json and not quiet:
                    render_info(f"Trying fallback model: {m}")
            try:
                if streamed:
                    from termux_agent.ui.renderer import PlainStreamPrinter

                    printer = PlainStreamPrinter()
                    answer = _run_guarded(agent, prompt, _log_tool, timeout, on_text_delta=printer.feed)
                    printer.flush()
                else:
                    answer = _run_guarded(agent, prompt, _log_tool, timeout)
                break
            except KeyboardInterrupt:
                raise
            except TimeoutError:
                raise
            except Exception as e:  # noqa: BLE001
                if idx == len(models_to_try) - 1:
                    raise
                if not as_json and not quiet:
                    render_error(f"Model {m} failed: {e}")
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
        if logger:
            logger("error", {"type": "timeout"})
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
    if getattr(agent, "messages", None) and not no_save:
        from termux_agent.session import record_messages

        record_messages(agent.messages, agent.provider.name, agent.provider.model)
    if logger:
        logger("done", {"answer": answer, "tool_calls": len(tool_log)})
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


def _attach_agent_context(agent: Agent, context_text: str) -> None:
    if not context_text:
        return
    agent.system_prompt += f"\n\n[Device context]\n{context_text}"
    if agent.messages and agent.messages[0].get("role") == "system":
        agent.messages[0]["content"] = agent.system_prompt


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


def cmd_bench(cfg: dict, provider_name: str | None = None, timeout: int = 60, as_json: bool = False) -> int:
    """Time one tiny prompt against each model of a provider (best-effort)."""
    import time

    from termux_agent.ui.renderer import render_error, render_info

    provider_name = provider_name or cfg.get("provider", "zen")
    models = (cfg.get("providers", {}).get(provider_name, {}).get("models") or [])
    if not models:
        render_error(f"Provider '{provider_name}' has no preset models to benchmark.")
        return 1
    if not as_json:
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
    if as_json:
        import json as _json

        print(_json.dumps(
            {"provider": provider_name, "models": [
                {"model": m, "seconds": round(dt, 2), "chars": ch, "ok": ok}
                for m, dt, ch, ok in results
            ]},
            ensure_ascii=False,
        ))
        return 0
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


def _run_logger(path: str):
    """Return a callable that appends timestamped JSON lines to the log file."""

    def _write(kind: str, data: dict) -> None:
        import datetime
        import json as _json

        line = _json.dumps(
            {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind, **data},
            ensure_ascii=False,
        )
        try:
            with open(Path(path).expanduser(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    return _write


def _git_context(cwd: Path) -> str:
    """Best-effort summary of repo state for the agent (empty if not a git repo)."""
    import subprocess

    if not (cwd / ".git").exists():
        return ""

    def run(*args: str) -> str:
        try:
            p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
            return p.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""

    parts = []
    st = run("status", "--short")
    if st:
        parts.append(f"git status:\n{st}")
    stat = run("diff", "--stat")
    if stat:
        parts.append(f"git diff --stat:\n{stat}")
    log = run("log", "--oneline", "-5")
    if log:
        parts.append(f"git log (last 5):\n{log}")
    return "\n\n".join(parts)


def _split_tools(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [t.strip() for t in value.split(",") if t.strip()]


def _disabled_groups_from(args) -> list[str]:
    groups: list[str] = []
    if getattr(args, "no_shell", False):
        groups.append("shell")
    if getattr(args, "no_web", False):
        groups.append("web")
    if getattr(args, "no_git", False):
        groups.append("git")
    return groups


def _allow_dirs_from(args) -> list[str] | None:
    return list(getattr(args, "allow_dir", None) or []) or None


def _batch_run_one(cfg, provider, model, auto_accept, timeout, disabled_groups, max_output_chars, command_timeout, agent_name, working_dir, only_tools, allow_dirs, p):
    """Run a single --batch prompt (module-level so tests can replace it)."""
    try:
        agent = build_agent(cfg, provider, model, auto_accept=auto_accept, disabled_groups=disabled_groups, max_output_chars=max_output_chars, command_timeout=command_timeout, agent_name=agent_name, working_dir=working_dir, only_tools=only_tools, allow_dirs=allow_dirs)
        answer = _run_guarded(agent, p, lambda *a, **k: None, timeout)
    except Exception as e:  # noqa: BLE001
        return {"prompt": p, "answer": None, "error": str(e)}
    return {"prompt": p, "answer": answer}


def cmd_batch(
    cfg: dict,
    prompts_file: str,
    provider: str | None,
    model: str | None,
    output: str | None = None,
    auto_accept: bool = False,
    timeout: int | None = None,
    as_json: bool = False,
    disabled_groups: list[str] | None = None,
    max_output_chars: int | None = None,
    command_timeout: int | None = None,
    agent_name: str | None = None,
    working_dir: str | None = None,
    only_tools: list[str] | None = None,
    workers: int = 1,
    allow_dirs: list[str] | None = None,
    fail_fast: bool = False,
    notify: bool = False,
) -> int:
    """Run one one-shot per line of a prompts file (blank lines skipped; '-' reads stdin)."""
    import json as _json

    from termux_agent.ui.renderer import render_error, render_info

    if prompts_file == "-":
        import sys as _sys

        text = _sys.stdin.read()
    else:
        try:
            text = Path(prompts_file).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            render_error(f"Cannot read --batch file: {e}")
            return 1
    prompts = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not prompts:
        render_error(f"--batch {'stdin' if prompts_file == '-' else 'file'} is empty: {prompts_file}")
        return 1

    def _run_one(p: str) -> dict:
        return _batch_run_one(
            cfg,
            provider,
            model,
            auto_accept,
            timeout,
            disabled_groups,
            max_output_chars,
            command_timeout,
            agent_name,
            working_dir,
            only_tools,
            allow_dirs,
            p,
        )

    results: list[dict] = []
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            results = list(ex.map(_run_one, prompts))
    else:
        for i, p in enumerate(prompts, start=1):
            if not as_json:
                render_info(f"[{i}/{len(prompts)}] {p[:60]}")
            r = _run_one(p)
            results.append(r)
            if r.get("error"):
                if not as_json:
                    render_error(f"  -> failed: {r['error']}")
                if fail_fast:
                    if output:
                        Path(output).write_text(_json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                        render_info(f"Partial results written to {output}")
                    elif as_json:
                        print(_json.dumps({"results": results, "fail_fast": True}, ensure_ascii=False))
                    if notify:
                        from termux_agent.notify import notify as _notify

                        _notify(f"Batch failed at prompt {i}/{len(prompts)}: {r['error'][:120]}")
                    return 1
            else:
                if not as_json:
                    render_info(f"  -> {r['answer'][:80]}")
    if output:
        Path(output).write_text(_json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        render_info(f"Results written to {output}")
    elif as_json:
        print(_json.dumps({"results": results}, ensure_ascii=False))
    if notify:
        from termux_agent.notify import notify as _notify

        failed = sum(1 for r in results if r.get("error"))
        _notify(f"Batch done: {len(results) - failed}/{len(results)} succeeded" + (f", {failed} failed" if failed else ""))
    return 0


def cmd_watch(
    cfg: dict,
    prompt: str,
    provider: str | None,
    model: str | None,
    interval: int,
    with_screenshot: bool = False,
    auto_accept: bool = False,
    timeout: int | None = None,
    disabled_groups: list[str] | None = None,
    max_output_chars: int | None = None,
    command_timeout: int | None = None,
    agent_name: str | None = None,
    working_dir: str | None = None,
    only_tools: list[str] | None = None,
    context: bool = False,
    allow_dirs: list[str] | None = None,
    screenshot_dir: str | None = None,
    max_rounds: int | None = None,
    notify: bool = False,
    diff: bool = False,
    as_json: bool = False,
    output: str | None = None,
    exit_on_change: bool = False,
) -> int:
    """Re-run a one-shot task every N seconds until Ctrl+C. Optionally re-attach a screenshot."""
    import json as _json
    import time

    from termux_agent.ui.renderer import render_answer, render_tool_use

    agent = build_agent(cfg, provider, model, auto_accept=auto_accept, disabled_groups=disabled_groups, max_output_chars=max_output_chars, command_timeout=command_timeout, agent_name=agent_name, working_dir=working_dir, only_tools=only_tools, allow_dirs=allow_dirs)
    if context:
        from termux_agent.notify import device_context

        _attach_agent_context(agent, device_context())
    if max_rounds:
        if not as_json:
            render_info(f"Watching every {interval}s — up to {max_rounds} round(s); press Ctrl+C to stop.")
    else:
        if not as_json:
            render_info(f"Watching every {interval}s — press Ctrl+C to stop.")
    round_no = 0
    last_answer: str | None = None
    try:
        while max_rounds is None or round_no < max_rounds:
            round_no += 1
            if not diff and not as_json:
                render_info(f"\n--- round {round_no} ---")
            p = prompt
            if with_screenshot:
                from termux_agent.notify import screenshot

                shot_dir = Path(screenshot_dir).expanduser() if screenshot_dir else None
                if shot_dir:
                    shot_dir.mkdir(parents=True, exist_ok=True)
                    img = screenshot(str(shot_dir / f"screenshot-{int(__import__('time').time())}.png"))
                else:
                    img = screenshot()
                if img:
                    p = f"{prompt}\n\n[image: {img}]"
                    if not as_json:
                        render_info(f"Attached screenshot: {img}")
                elif not as_json:
                    render_error("Screenshot failed this round — continuing without it.")
            try:
                answer = _run_guarded(agent, p, render_tool_use, timeout)
            except TimeoutError:
                if diff and not as_json:
                    render_info(f"\n--- round {round_no} (timed out) ---")
                if as_json:
                    print(_json.dumps({"round": round_no, "error": f"timed out after {timeout}s"}, ensure_ascii=False))
                else:
                    render_error(f"Round {round_no} timed out after {timeout}s.")
                if notify:
                    from termux_agent.notify import notify as _notify

                    _notify(f"Round {round_no} timed out")
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001
                if diff and not as_json:
                    render_info(f"\n--- round {round_no} (failed) ---")
                if as_json:
                    print(_json.dumps({"round": round_no, "error": str(e)}, ensure_ascii=False))
                else:
                    render_error(f"Round {round_no} failed: {e}")
                if notify:
                    from termux_agent.notify import notify as _notify

                    _notify(f"Round {round_no} failed: {e}")
            else:
                if diff and last_answer is not None and answer == last_answer:
                    if not as_json:
                        render_info(f"round {round_no}: answer unchanged — skipping.")
                    if max_rounds is None or round_no < max_rounds:
                        time.sleep(interval)
                    continue
                if exit_on_change and last_answer is not None and answer != last_answer:
                    if as_json:
                        print(_json.dumps({"round": round_no, "answer": answer, "changed": True}, ensure_ascii=False))
                    else:
                        render_info(f"round {round_no}: answer changed — exiting.")
                    if notify:
                        from termux_agent.notify import notify as _notify

                        _notify(f"Answer changed at round {round_no}: {answer[:120]}")
                    return 0
                if diff and not as_json:
                    render_info(f"\n--- round {round_no} (changed) ---")
                last_answer = answer
                if output:
                    Path(output).write_text(answer + "\n", encoding="utf-8")
                if as_json:
                    print(_json.dumps({"round": round_no, "answer": answer}, ensure_ascii=False))
                else:
                    render_answer(answer)
                if notify:
                    from termux_agent.notify import notify as _notify

                    _notify(f"Round {round_no} done: {answer[:120]}")
            if max_rounds is None or round_no < max_rounds:
                time.sleep(interval)
    except KeyboardInterrupt:
        if not as_json:
            render_info("\nStopped.")
        return 0
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


def cmd_export(ref: str | None = None, as_markdown: bool = False) -> int:
    """Print a session as portable JSON (default: latest)."""
    from termux_agent.session import export_session

    try:
        data = export_session(ref)
    except FileNotFoundError:
        render_error("Session not found.")
        return 1
    if as_markdown:
        print(_session_to_markdown(data))
        return 0
    import json as _json

    print(_json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _session_to_markdown(data: dict) -> str:
    lines = [
        f"# Session {data.get('id', '?')}",
        "",
        f"- provider: {data.get('provider', '')}",
        f"- model: {data.get('model', '')}",
        f"- messages: {len(data.get('messages', []))}",
        "",
    ]
    for m in data.get("messages", []):
        role = m.get("role", "?")
        content = m.get("content", "")
        if role == "system":
            lines += ["### system", "```", str(content), "```", ""]
        elif role == "tool":
            lines += [f"### tool ({m.get('name', '')})", "```", str(content)[:4000], "```", ""]
        else:
            lines += [f"### {role}", str(content), ""]
    return "\n".join(lines)


def cmd_show(ref: str | None, as_json: bool = False, output: str | None = None) -> int:
    """Show a full session transcript (default: latest)."""
    import json as _json

    from termux_agent.session import export_session

    try:
        data = export_session(ref)
    except FileNotFoundError:
        render_error("Session not found.")
        return 1
    if output:
        try:
            if as_json:
                Path(output).write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                Path(output).write_text(_session_to_markdown(data) + "\n", encoding="utf-8")
        except OSError as e:
            render_error(f"Cannot write output file {output}: {e}")
            return 1
        render_info(f"Transcript written to {output}")
        return 0
    if as_json:
        print(_json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(_session_to_markdown(data))
    chars = sum(len(str(m.get("content", ""))) for m in data.get("messages", []))
    render_info(f"\n~{max(1, chars // 4)} tokens estimated ({chars} message characters, chars/4 heuristic).")
    return 0


def cmd_tokens(path: str | None, text: str | None = None, as_json: bool = False) -> int:
    """Estimate token usage of a file or inline text."""
    import sys as _sys

    if path:
        try:
            text = Path(path).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            if as_json:
                import json as _json

                print(_json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            else:
                render_error(f"Cannot read file: {e}")
            return 1
    if not text:
        text = _sys.stdin.read() if not _sys.stdin.isatty() else ""
    chars = len(text)
    estimated = max(1, chars // 4)
    if as_json:
        import json as _json

        print(_json.dumps({"ok": True, "chars": chars, "tokens": estimated}, ensure_ascii=False))
        return 0
    render_info(f"{chars} characters, ~{estimated} tokens (rough heuristic: chars/4).")
    return 0


def cmd_import(path: str, dry_run: bool = False, as_json: bool = False) -> int:
    """Import a portable session JSON file and save it as a session (--dry-run validates only)."""
    import json as _json

    from termux_agent.session import import_session

    try:
        if path == "-":
            import sys as _sys

            data = _json.load(_sys.stdin)
        else:
            with open(path, encoding="utf-8") as f:
                data = _json.load(f)
        sid = None if dry_run else import_session(data)
    except FileNotFoundError:
        if as_json:
            print(_json.dumps({"ok": False, "error": f"File not found: {path}"}, ensure_ascii=False))
        else:
            render_error(f"File not found: {path}")
        return 1
    except (ValueError, _json.JSONDecodeError) as e:
        if as_json:
            print(_json.dumps({"ok": False, "error": f"Invalid session file: {e}"}, ensure_ascii=False))
        else:
            render_error(f"Invalid session file: {e}")
        return 1
    n = len(data.get("messages", []))
    if as_json:
        print(_json.dumps({"ok": True, "dry_run": dry_run, "session": sid, "messages": n}, ensure_ascii=False))
        return 0
    if dry_run:
        render_info(f"Valid session file: {n} message(s). Nothing imported.")
        return 0
    render_info(f"Imported session {sid} ({n} messages)")
    return 0


def cmd_prune(keep: int, as_json: bool = False, dry_run: bool = False) -> int:
    import json as _json

    from termux_agent.session import list_sessions, prune_sessions

    removed = 0 if dry_run else prune_sessions(max(0, keep))
    if dry_run:
        removed = max(0, len(list_sessions()) - max(0, keep))
    if as_json:
        print(_json.dumps({"removed": removed, "kept": max(0, keep), "dry_run": dry_run}, ensure_ascii=False))
        return 0
    if dry_run:
        render_info(f"Would remove {removed} old session(s), keeping the newest {max(0, keep)}. (dry run)")
    else:
        render_info(f"Removed {removed} old session(s), keeping the newest {max(0, keep)}.")
    return 0


def cmd_config_show(cfg: dict, as_json: bool = False, redact: bool = False) -> int:
    import json as _json
    import yaml as _yaml

    if redact:
        cfg = _redact_cfg(cfg)
    if as_json:
        print(_json.dumps(cfg, ensure_ascii=False, default=str))
        return 0
    print(_yaml.safe_dump(cfg, sort_keys=False))
    return 0


def _redact_cfg(cfg: dict) -> dict:
    """Return a copy of the config with secrets masked for safe display."""
    import copy

    out = copy.deepcopy(cfg)
    providers = out.setdefault("providers", {})
    for p, pc in providers.items():
        if isinstance(pc, dict):
            for key in ("api_key", "api_key_env", "key", "token"):
                if key in pc and pc[key]:
                    pc[key] = "***"
    return out


def cmd_prune_days(days: int, as_json: bool = False, dry_run: bool = False, keep: int = 0) -> int:
    import json as _json
    import time

    from termux_agent.session import list_sessions, prune_days

    if keep > 0:
        by_mtime = sorted(list_sessions(), key=lambda s: s.stat().st_mtime, reverse=True)
        cut = by_mtime[keep:]
        if dry_run:
            removed = sum(1 for s in cut if s.stat().st_mtime < time.time() - max(1, days) * 86400)
        else:
            removed = 0
            for s in cut:
                if s.stat().st_mtime < time.time() - max(1, days) * 86400:
                    s.unlink(missing_ok=True)
                    removed += 1
    elif dry_run:
        cutoff = time.time() - max(1, days) * 86400
        removed = sum(1 for s in list_sessions() if s.stat().st_mtime < cutoff)
    else:
        removed = prune_days(max(1, days))
    if as_json:
        print(_json.dumps({"removed": removed, "days": days, "dry_run": dry_run, "keep": keep}, ensure_ascii=False))
        return 0
    if dry_run:
        render_info(f"Would remove {removed} session(s) older than {days} day(s), keeping the {keep} newest." if keep else f"Would remove {removed} session(s) older than {days} day(s). (dry run)")
    else:
        render_info(f"Removed {removed} session(s) older than {days} day(s), keeping the {keep} newest." if keep else f"Removed {removed} session(s) older than {days} day(s).")
    return 0


def cmd_list_tools() -> int:
    from termux_agent.tools.base import tool_specs

    specs = tool_specs()
    for s in specs:
        render_info(f"{s.name}: {s.description}")
    render_info(f"\n{len(specs)} tools registered.")
    return 0


def cmd_forget(ref: str | None = None, as_json: bool = False) -> int:
    import json as _json

    from termux_agent.session import delete_session

    removed = delete_session(ref)
    if not removed:
        if as_json:
            print(_json.dumps({"ok": False, "error": "not found"}, ensure_ascii=False))
        else:
            render_error("Session not found.")
        return 1
    if as_json:
        print(_json.dumps({"ok": True, "deleted": removed.stem}, ensure_ascii=False))
    else:
        render_info(f"Deleted session {removed.stem}.")
    return 0


def cmd_export_all(target_dir: str, as_markdown: bool = False, as_json: bool = False) -> int:
    import json as _json

    from termux_agent import __version__
    from termux_agent.session import export_session, list_sessions

    out = Path(target_dir)
    if as_json:
        sessions = []
        for s in list_sessions():
            sessions.append(export_session(s.stem))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            _json.dumps({"app": "termux-agent", "version": __version__, "sessions": sessions}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        render_info(f"Exported {len(sessions)} session(s) to {out}.")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    if as_markdown:
        md_dir = out / "markdown"
        md_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for s in list_sessions():
        data = export_session(s.stem)
        if as_markdown:
            (md_dir / f"{s.stem}.md").write_text(_session_to_markdown(data), encoding="utf-8")
        else:
            (out / f"{s.stem}.json").write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        count += 1
    render_info(f"Exported {count} session(s) to {out}." + (" (markdown under markdown/)" if as_markdown else ""))
    return 0


def cmd_summarize(
    cfg: dict,
    ref: str | None,
    provider: str | None,
    model: str | None,
    output: str | None = None,
    as_json: bool = False,
    timeout: int | None = None,
    notify: bool = False,
) -> int:
    """Have the agent summarize a session transcript (default: latest)."""
    import json as _json

    from termux_agent.session import export_session
    from termux_agent.ui.renderer import render_answer, render_error

    try:
        data = export_session(ref)
    except FileNotFoundError:
        render_error("Session not found.")
        return 1
    sid = data.get("id", "?")
    transcript = []
    for m in data.get("messages", []):
        role = m.get("role", "?")
        if role == "system":
            continue
        content = str(m.get("content", ""))[:2000]
        if not content.strip():
            continue
        transcript.append(f"{role.upper()}: {content}")
    if not transcript:
        render_error("Session has no usable messages to summarize.")
        return 1
    prompt = (
        "Summarize the following conversation in a clear, structured way: "
        "main topic, decisions, files/commands touched, and open questions. "
        "Keep it under 200 words.\n\n"
        + "\n\n".join(transcript)
    )
    try:
        agent = build_agent(cfg, provider, model, auto_accept=True)
        summary = _run_guarded(agent, prompt, lambda *a, **k: None, timeout)
    except Exception as e:  # noqa: BLE001
        render_error(f"Summarize failed: {e}")
        return 1
    if output:
        Path(output).write_text(summary + "\n", encoding="utf-8")
        render_info(f"Summary written to {output}")
    if notify:
        from termux_agent.notify import notify as _notify

        _notify(f"Summary done: {summary[:120]}")
    if as_json:
        print(_json.dumps({"ok": True, "session": sid, "summary": summary}, ensure_ascii=False))
    elif not output:
        render_answer(summary)
    return 0


def cmd_bundle(target_dir: str) -> int:
    """Back up config, memory, and all sessions into a portable directory (or a gzipped tar to stdout with '-')."""
    import json as _json
    import shutil

    from termux_agent.agent import MEMORY_FILE
    from termux_agent.session import SESSIONS_DIR, list_sessions

    def _collect() -> list[Path]:
        files = []
        if CONFIG_FILE.is_file():
            files.append(CONFIG_FILE)
        if MEMORY_FILE.is_file():
            files.append(MEMORY_FILE)
        for s in list_sessions():
            files.append(s)
        return files

    if target_dir == "-":
        import io
        import tarfile

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for f in _collect():
                tf.add(f, arcname=f.name)
        import sys as _sys

        _sys.stdout.buffer.write(buf.getvalue())
        _sys.stdout.buffer.flush()
        return 0

    out = Path(target_dir)
    out.mkdir(parents=True, exist_ok=True)
    copied = []
    if CONFIG_FILE.is_file():
        shutil.copy2(CONFIG_FILE, out / CONFIG_FILE.name)
        copied.append(CONFIG_FILE.name)
    if MEMORY_FILE.is_file():
        shutil.copy2(MEMORY_FILE, out / MEMORY_FILE.name)
        copied.append(MEMORY_FILE.name)
    ses_dir = out / "sessions"
    ses_dir.mkdir(parents=True, exist_ok=True)
    n_sessions = 0
    for s in list_sessions():
        shutil.copy2(s, ses_dir / s.name)
        n_sessions += 1
    manifest = {
        "app": "termux-agent",
        "version": __version__,
        "config": CONFIG_FILE.name if CONFIG_FILE.is_file() else None,
        "memory": MEMORY_FILE.name if MEMORY_FILE.is_file() else None,
        "sessions": n_sessions,
    }
    (out / "manifest.json").write_text(_json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    render_info(f"Bundled {n_sessions} session(s) + config + memory into {out}.")
    return 0


def cmd_restore(bundle_dir: str) -> int:
    """Restore config, memory, and sessions from a bundle directory (or a gzipped tar on stdin with '-')."""
    import json as _json
    import shutil

    from termux_agent.agent import MEMORY_FILE
    from termux_agent.session import SESSIONS_DIR

    if bundle_dir == "-":
        import io
        import tarfile
        import sys as _sys
        import tempfile

        buf = io.BytesIO(_sys.stdin.buffer.read())
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=buf, mode="r:gz") as tf:
                tf.extractall(tmp, filter="data")
            src = Path(tmp)
            return _restore_from_dir(src)

    return _restore_from_dir(Path(bundle_dir))


def _restore_from_dir(src: Path) -> int:
    """Restore config, memory, and sessions from an extracted bundle directory."""
    import json as _json
    import shutil

    from termux_agent.agent import MEMORY_FILE
    from termux_agent.session import SESSIONS_DIR

    if not (src / "manifest.json").is_file():
        render_error(f"No manifest.json found in {src} — not a termux-agent bundle.")
        return 1
    manifest = _json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    restored = []
    for name in ("config.yaml", "memory.md"):
        f = src / name
        if f.is_file():
            shutil.copy2(f, CONFIG_DIR / name)
            restored.append(name)
    for f in sorted((src / "sessions").glob("*.jsonl")) if (src / "sessions").is_dir() else []:
        shutil.copy2(f, SESSIONS_DIR / f.name)
        restored.append(f"session/{f.name}")
    render_info(f"Restored {len(restored)} item(s) from {src} ({manifest.get('app', '?')} v{manifest.get('version', '?')}).")
    return 0


def cmd_rerun(
    cfg: dict,
    ref: str | None,
    provider: str | None,
    model: str | None,
    output: str | None = None,
    as_json: bool = False,
    timeout: int | None = None,
    attach: list[str] | None = None,
    diff: bool = False,
    notify: bool = False,
) -> int:
    """Re-run the last user prompt of a session with the current model (fresh run)."""
    import json as _json

    from termux_agent.session import export_session
    from termux_agent.ui.renderer import render_answer, render_error

    try:
        data = export_session(ref)
    except FileNotFoundError:
        render_error("Session not found.")
        return 1
    last_user = next(
        (str(m.get("content", "")) for m in reversed(data.get("messages", [])) if m.get("role") == "user"),
        "",
    )
    old_answer = next(
        (str(m.get("content", "")) for m in reversed(data.get("messages", [])) if m.get("role") == "assistant"),
        "",
    )
    if not last_user.strip():
        render_error("Session has no user prompt to re-run.")
        return 1
    if attach:
        for f in attach:
            try:
                content = Path(f).expanduser().read_text(encoding="utf-8")
            except OSError as e:
                render_error(f"Cannot read --attach file: {e}")
                return 1
            last_user = f"{last_user}\n\n<file name={f}>\n{content}\n</file>"
        render_info(f"Attached {len(attach)} file(s) to the re-run prompt.")
    try:
        agent = build_agent(cfg, provider, model, auto_accept=True)
        answer = _run_guarded(agent, last_user, lambda *a, **k: None, timeout)
    except Exception as e:  # noqa: BLE001
        render_error(f"Rerun failed: {e}")
        return 1
    if output:
        Path(output).write_text(answer + "\n", encoding="utf-8")
        render_info(f"Answer written to {output}")
    if notify:
        from termux_agent.notify import notify as _notify

        _notify(f"Rerun done: {answer[:120]}")
    if as_json:
        print(_json.dumps({"ok": True, "session": data.get("id", "?"), "prompt": last_user, "answer": answer}, ensure_ascii=False))
    elif diff:
        import difflib

        from termux_agent.ui.renderer import console as _console

        diff_lines = list(
            difflib.unified_diff(
                old_answer.splitlines(),
                answer.splitlines(),
                fromfile="previous",
                tofile="new",
                lineterm="",
            )
        )
        if diff_lines:
            for line in diff_lines:
                _console.print(line)
        else:
            render_info("No change between the previous and new answer.")
    elif not output:
        render_answer(answer)
    return 0


def cmd_show_system_prompt(
    cfg: dict,
    provider: str | None,
    model: str | None,
    agent_name: str | None = None,
    working_dir: str | None = None,
    no_tools: bool = False,
    rules_file: str | None = None,
    system_prompt_file: str | None = None,
    context: bool = False,
    disabled_groups: list[str] | None = None,
) -> int:
    """Print the effective system prompt without running a turn."""
    extra_rules = ""
    if rules_file:
        try:
            extra_rules = Path(rules_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            render_error(f"Cannot read --rules file: {e}")
            return 1
    sys_prompt = None
    if system_prompt_file:
        try:
            sys_prompt = Path(system_prompt_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            render_error(f"Cannot read --system-prompt file: {e}")
            return 1
    agent = build_agent(cfg, provider, model, False, agent_name, working_dir, disabled_groups=disabled_groups, extra_rules=extra_rules, system_prompt=sys_prompt, no_tools=no_tools)
    if context:
        from termux_agent.notify import device_context

        _attach_agent_context(agent, device_context())
    from termux_agent.ui.renderer import console

    console.print(agent.system_prompt)
    return 0


def cmd_cleanup() -> int:
    """Remove leftover screenshot-*.png files from the current directory."""
    removed = 0
    for p in Path.cwd().glob("screenshot-*.png"):
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    render_info(f"Removed {removed} leftover screenshot file(s).")
    return 0


def cmd_serve(
    cfg: dict,
    host: str,
    port: int,
    provider: str | None,
    model: str | None,
    auto_accept: bool,
    token: str | None,
    background: bool = False,
    pidfile: str | None = None,
    log_file: str | None = None,
) -> int:
    """Run the HTTP API server, optionally detached in the background."""
    if background:
        import subprocess
        import sys

        from termux_agent.server import serve as _unused  # noqa: F401 (validate import)

        actual_port = port if port else 8787
        log_path = CONFIG_DIR / "server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "termux_agent", "--serve", "--host", str(host), "--port", str(actual_port)]
        if provider:
            cmd += ["--provider", provider]
        if model:
            cmd += ["--model", model]
        if auto_accept:
            cmd += ["--yes"]
        if token:
            cmd += ["--token", token]
        if log_file:
            cmd += ["--log", log_file]
        with open(log_path, "a", encoding="utf-8") as logf:
            proc = subprocess.Popen(
                cmd,
                stdout=logf,
                stderr=logf,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        pid_file = Path(pidfile) if pidfile else CONFIG_DIR / "server.pid"
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        render_info(
            f"Server started in background (pid {proc.pid}) on http://{host}:{actual_port}. "
            f"Log: {log_path}. Pid file: {pid_file}. Use 'termux-agent --serve-stop' to stop it."
        )
        return 0
    from termux_agent.server import serve

    return serve(cfg, host=host, port=port, provider=provider, model=model, auto_accept=auto_accept, token=token, log_file=log_file)


def cmd_serve_stop(pidfile: str | None = None) -> int:
    """Stop a background server started with --serve --background."""
    import signal

    pid_file = Path(pidfile) if pidfile else CONFIG_DIR / "server.pid"
    if not pid_file.is_file():
        render_error(f"No server pid file found at {pid_file}.")
        return 1
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        render_error(f"Invalid pid file: {pid_file}")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        render_info(f"Process {pid} already gone.")
    pid_file.unlink(missing_ok=True)
    render_info(f"Stopped server pid {pid}.")
    return 0


def cmd_cron(schedule: str, prompt: str, command: str | None = None, as_json: bool = False) -> int:
    """Print a ready-to-add cron line running termux-agent one-shot."""
    import json as _json

    command = command or f"termux-agent --no-save --quiet {prompt!r}"
    line = f"{schedule} cd {Path.cwd()} && {command} >> ~/.termux-agent/cron.log 2>&1"
    if as_json:
        print(_json.dumps({"schedule": schedule, "command": command, "line": line}, ensure_ascii=False))
        return 0
    print(line)
    return 0


def cmd_sessions(search: str | None = None, as_json: bool = False, limit: int = 20) -> int:
    import json as _json

    from termux_agent.session import list_sessions, read_session

    sessions = list_sessions()
    needle = search.lower() if search else ""
    items = []
    for s in sessions[:200]:
        recs = read_session(s)
        first_user = next((r["content"] for r in recs if r.get("role") == "user"), "")
        if needle:
            haystack = " ".join(
                str(r.get("content", ""))
                for r in recs
                if r.get("role") in ("user", "assistant")
            ).lower()
            if needle not in haystack:
                continue
        info = next((r for r in recs if r.get("provider")), {})
        items.append(
            {
                "id": s.stem,
                "provider": info.get("provider") or "",
                "model": info.get("model") or "",
                "messages": len(recs),
                "first": first_user[:100],
            }
        )
        if len(items) >= max(1, limit):
            break
    if as_json:
        print(_json.dumps({"sessions": items}, ensure_ascii=False))
        return 0
    if not items:
        render_info("No sessions found." if needle else "No sessions saved yet in ~/.termux-agent/sessions/.")
        return 0
    for it in items:
        render_info(f"{it['id']}  [{it['messages']} messages]  {it['first'][:60]}")
    if needle:
        render_info(f"\n{len(items)} matching session(s).")
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
    stream: bool = False,
    disabled_groups: list[str] | None = None,
    max_output_chars: int | None = None,
    command_timeout: int | None = None,
    git_context: bool = False,
    allow_dirs: list[str] | None = None,
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
    extra_rules = ""
    if git_context:
        cwd = Path(working_dir).expanduser().resolve() if working_dir else resolve_working_dir(cfg)
        extra_rules = _git_context(cwd)
    agent = build_agent(cfg, provider_name, model, auto_accept, agent_name, working_dir, temperature, max_tool_rounds, readonly, max_context_tokens, no_tools=False, retries=None, no_fallback=False, extra_rules=extra_rules or None, system_prompt=None, disabled_groups=disabled_groups, max_output_chars=max_output_chars, command_timeout=command_timeout, allow_dirs=allow_dirs)
    agent.messages = [{"role": "system", "content": agent.system_prompt}] + history
    if prompt:
        streamed = stream and not as_json and not quiet

        def _log(name: str, args_str: str) -> None:
            if not as_json and not quiet:
                render_tool_use(name, args_str)

        if streamed:
            from termux_agent.ui.renderer import PlainStreamPrinter

            printer = PlainStreamPrinter()
            answer = agent.run(prompt, on_tool_use=_log, on_text_delta=printer.feed)
            printer.flush()
        else:
            answer = agent.run(prompt, on_tool_use=_log)
        _maybe_notify(cfg, "Resume done", answer, as_json)
        if as_json:
            _emit_json({"ok": True, "answer": answer, "session": path.stem}, agent)
        elif quiet:
            print(answer)
        elif not streamed:
            render_answer(answer)
        return 0
    if as_json or quiet:
        render_error("--json/--quiet require a prompt with --resume.")
        return 2
    Repl(agent, provider_name=provider_name, model=agent.provider.model, agent_name=agent_name).run()
    return 0


def cmd_list_providers(cfg: dict, as_json: bool = False) -> int:
    import json as _json

    if as_json:
        items = [
            {"name": n, "type": pc.get("type"), "models": pc.get("models") or []}
            for n, pc in cfg.get("providers", {}).items()
        ]
        print(_json.dumps({"providers": items}, ensure_ascii=False))
        return 0
    for name, pc in cfg.get("providers", {}).items():
        models = ", ".join(pc.get("models") or [])
        render_info(f"{name:12} {pc.get('type'):16} models: {models}")
    return 0


def cmd_list_agents(cfg: dict, as_json: bool = False) -> int:
    import json as _json

    if as_json:
        items = [
            {"name": n, "description": spec.get("description", ""), "tools": spec.get("tools") or []}
            for n, spec in cfg.get("agents", {}).items()
        ]
        print(_json.dumps({"agents": items}, ensure_ascii=False))
        return 0
    for name, spec in cfg.get("agents", {}).items():
        tools = spec.get("tools") or []
        label = "all tools" if not tools else ", ".join(tools)
        render_info(f"{name:10} {spec.get('description', '')}  [{label}]")
    return 0


def cmd_list_models(cfg: dict, provider_name: str | None = None, as_json: bool = False) -> int:
    import json as _json

    name = provider_name or cfg.get("provider", "zen")
    try:
        provider = create_provider(name, cfg)
    except ConfigError as e:
        if as_json:
            print(_json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            render_error(str(e))
        return 1
    live = provider.list_models()
    if live:
        if as_json:
            print(_json.dumps({"provider": name, "models": live}, ensure_ascii=False))
        else:
            render_info(f"Models for '{name}':")
            for m in live:
                render_info(f"  {m}")
        return 0
    if as_json:
        print(_json.dumps({"provider": name, "models": cfg.get("providers", {}).get(name, {}).get("models", [])}, ensure_ascii=False))
        return 0
    render_info(f"'{name}' does not expose a live model list; showing presets:")
    for m in cfg.get("providers", {}).get(name, {}).get("models", []):
        render_info(f"  {m}")
    return 0


def cmd_doctor(cfg: dict, network: bool = False, as_json: bool = False, termux: bool = False, update: bool = False) -> int:
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
    try:
        import shutil as _sh

        du = _sh.disk_usage("/")
        free_gb = du.free / (1024 ** 3)
        add("free disk (/)", True, f"{free_gb:.1f} GiB free of {du.total / (1024 ** 3):.1f} GiB")
    except OSError as e:
        add("free disk (/)", False, str(e))
    try:
        from termux_agent.session import SESSIONS_DIR, list_sessions

        sess = list_sessions()
        total = sum(s.stat().st_size for s in sess)
        add("sessions", True, f"{len(sess)} stored, {total / 1024:.1f} KiB total")
    except OSError as e:
        add("sessions", False, str(e))
    pname = cfg.get("provider", "zen")
    pc = cfg.get("providers", {}).get(pname, {})
    add("active provider", True, f"{pname} ({pc.get('type')})")
    configured_model = cfg.get("model", "")
    if configured_model and pc.get("models"):
        if configured_model in pc["models"]:
            add("configured model", True, f"{configured_model} in provider's list")
        else:
            add(
                "configured model",
                False,
                f"{configured_model} NOT in {pname} models: {', '.join(map(str, pc['models'])) or 'none'}",
            )
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

    termux_start = len(checks)
    if termux:
        api_cmds = {
            "termux-notification": "notifications (--notify)",
            "termux-battery-status": "device context (--context)",
            "termux-wifi-connectioninfo": "wifi context (--context)",
            "termux-clipboard-get": "clipboard (--clip)",
            "termux-screenshot": "screen capture (--screenshot/--watch)",
            "termux-tts-speak": "speech (--speak)",
        }
        for cmd, purpose in api_cmds.items():
            found = shutil.which(cmd)
            add(f"termux-api: {cmd}", bool(found), f"{purpose} - {found or 'not installed (pkg install termux-api)'}")

    if update:
        latest = _latest_pypi_version()
        if latest is None:
            add("update check", False, "could not reach PyPI (offline?) - try --doctor-network")
        elif latest == __version__:
            add("update check", True, f"{__version__} is the latest")
        else:
            add("update check", False, f"{__version__} installed, {latest} available - pip install -U termux-agent")

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
    for c in checks[11:termux_start]:
        (render_info if c["ok"] else render_error)(f"  [{'OK' if c['ok'] else '!!'}]  {c['label']}" + (f": {c['detail']}" if c["detail"] else ""))
    if termux:
        render_info("== termux-api ==")
        for c in checks[termux_start:termux_start + 6]:
            (render_info if c["ok"] else render_error)(f"  [{'OK' if c['ok'] else '!!'}]  {c['label']}" + (f": {c['detail']}" if c["detail"] else ""))
    if update:
        render_info("== updates ==")
        for c in checks[termux_start + (6 if termux else 0):]:
            (render_info if c["ok"] else render_error)(f"  [{'OK' if c['ok'] else '!!'}]  {c['label']}" + (f": {c['detail']}" if c["detail"] else ""))
    render_info("\nIf you see [!!] markers, rerun with TERMUX_AGENT_DEBUG=1 for detailed logs.")
    return 1 if issues else 0


def _latest_pypi_version() -> str | None:
    """Return the latest published version on PyPI, or None if unreachable."""
    import json as _json
    import urllib.request

    try:
        with urllib.request.urlopen("https://pypi.org/pypi/termux-agent/json", timeout=10) as r:
            data = _json.loads(r.read())
        return str(data.get("info", {}).get("version"))
    except Exception:  # noqa: BLE001
        return None


def cmd_smoke(cfg: dict, provider: str | None, model: str | None, as_json: bool = False) -> int:
    """End-to-end smoke test: send a tiny prompt and verify the whole pipeline."""
    import json as _json
    import time

    from termux_agent.ui.renderer import render_tool_use

    try:
        agent = build_agent(cfg, provider, model, auto_accept=True)
    except (ConfigError, KeyError) as e:
        if as_json:
            print(_json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            render_error(f"Error: {e}")
        return 1
    if not as_json:
        render_info(
            f"Smoke test: provider={agent.provider.name} model={agent.provider.model} cwd={agent.ctx.working_dir}"
        )
    start = time.monotonic()
    try:
        answer = agent.run("Reply with exactly: OK", on_tool_use=render_tool_use)
    except Exception as e:  # noqa: BLE001
        if as_json:
            print(_json.dumps({"ok": False, "provider": agent.provider.name, "model": agent.provider.model, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        else:
            render_error(f"Smoke test FAILED: {type(e).__name__}: {e}")
        return 1
    elapsed = time.monotonic() - start
    usage = agent.usage
    ok = bool(answer.strip())
    _maybe_notify(cfg, "Smoke test " + ("OK" if ok else "FAILED"), answer)
    if as_json:
        print(
            _json.dumps(
                {
                    "ok": ok,
                    "provider": agent.provider.name,
                    "model": agent.provider.model,
                    "elapsed": round(elapsed, 2),
                    "usage": usage,
                    "answer": answer[:200],
                },
                ensure_ascii=False,
            )
        )
    else:
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
    parser.add_argument("--version", action="store_true", help="Show the version and exit")
    parser.add_argument("--help-json", action="store_true", help="Print the full CLI reference as machine-readable JSON and exit")
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
    parser.add_argument("--notify", action="store_true", help="Send a Termux notification when a one-shot task or --batch finishes, or after each --watch round (needs termux-api)")
    parser.add_argument("--wakelock", action="store_true", help="Hold a Termux wake lock while a one-shot task runs (needs termux-api)")
    parser.add_argument("--speak", action="store_true", help="Read the answer aloud with termux-tts-speak (needs termux-api)")
    parser.add_argument("--timeout", type=int, metavar="SECONDS", help="Abort a one-shot task if it takes longer than this")
    parser.add_argument("--output", metavar="FILE", help="Also write the answer to this file (plain text)")
    parser.add_argument("--clip", action="store_true", help="Use the clipboard as the prompt (needs termux-api)")
    parser.add_argument("--attach", metavar="FILE", action="append", help="Read a file's contents into the prompt (repeatable)")
    parser.add_argument("--screenshot", action="store_true", help="Attach a screenshot of the screen to the prompt (needs termux-api + screen share)")
    parser.add_argument("--screenshot-dir", metavar="DIR", help="Save screenshots into this directory instead of the current one")
    parser.add_argument("--cleanup", action="store_true", help="Delete leftover screenshot-*.png files in the current directory")
    parser.add_argument("--stream", action="store_true", help="Stream the answer to the terminal as it is generated (typewriter mode)")
    parser.add_argument("--no-stream", action="store_true", help="Force a non-streaming one-shot/resume even in a TTY")
    parser.add_argument("--rotate", action="store_true", help="On failure, retry with the next model in the provider's list (handy for free-tier rate limits)")
    parser.add_argument("--watch", type=int, metavar="SECONDS", help="Re-run the one-shot prompt every N seconds until Ctrl+C (combine with --screenshot)")
    parser.add_argument("--max-rounds", type=int, default=None, metavar="N", help="With --watch: stop after this many rounds")
    parser.add_argument("--diff", action="store_true", help="With --watch: only print/notify when the answer changes; with --rerun: show the diff vs the previous answer")
    parser.add_argument("--exit-on-change", action="store_true", help="With --watch: stop as soon as the answer differs from the previous round")
    parser.add_argument("--batch", metavar="FILE", help="Run one one-shot per line of the file (blank lines skipped; '-' reads stdin); --output writes results as JSON")
    parser.add_argument("--retries", type=int, metavar="N", help="Override transient retry count for network hiccups")
    parser.add_argument("--no-fallback", action="store_true", help="Disable fallback models on 429/errors (use only the selected model)")
    parser.add_argument("--rules", metavar="FILE", help="Add extra instructions to the system prompt (like AGENTS.md but per-invocation)")
    parser.add_argument("--system-prompt", dest="system_prompt_file", metavar="FILE", help="Replace the entire system prompt with the file contents (custom persona)")
    parser.add_argument("--show-system-prompt", action="store_true", help="Print the effective system prompt and exit (no turn runs)")
    parser.add_argument("--context", action="store_true", help="Add device context (battery/wifi/time, via termux-api) to the system prompt")
    parser.add_argument("--no-shell", action="store_true", help="Disable the run_command tool")
    parser.add_argument("--no-web", action="store_true", help="Disable web_fetch and web_search tools")
    parser.add_argument("--no-git", action="store_true", help="Disable all git tools")
    parser.add_argument("--no-save", action="store_true", help="Do not persist this one-shot run as a session")
    parser.add_argument("--no-memory", action="store_true", help="Run without the persistent memory file (~/.termux-agent/memory.md)")
    parser.add_argument("--git", action="store_true", dest="git_context", help="Inject the repo state (status/diff/log) into the system prompt")
    parser.add_argument("--show", metavar="SESSION", help="Show a full session transcript (default: latest); use --json for raw output")
    parser.add_argument("--tokens", metavar="FILE", help="Estimate the token count of a file (omit to read stdin)")
    parser.add_argument("--summarize", nargs="?", const="latest", metavar="SESSION", help="Have the agent summarize a session transcript (default: latest); --output saves it")
    parser.add_argument("--rerun", nargs="?", const="latest", metavar="SESSION", help="Re-run the last user prompt of a session as a fresh one-shot (default: latest); --output saves it")
    parser.add_argument("--bundle", metavar="DIR", help="Back up config, memory, and all sessions into a portable directory ('-' streams a gzipped tar to stdout)")
    parser.add_argument("--restore", metavar="DIR", help="Restore config, memory, and sessions from a bundle directory ('-' reads a gzipped tar from stdin)")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors (same as NO_COLOR=1)")
    parser.add_argument("--allow-dir", action="append", metavar="DIR", help="Grant the agent file access to an extra directory (repeatable)")
    parser.add_argument("--cron", metavar="SCHEDULE", help="Print a ready-to-add cron line, e.g. '*/10 * * * *'")
    parser.add_argument("--only-tools", metavar="LIST", help="Restrict the agent to exactly these comma-separated tool names, e.g. read_file,grep,glob")
    parser.add_argument("--log", metavar="FILE", help="Append a timestamped JSONL run log (tool calls, errors, result) for one-shot runs")
    parser.add_argument("--workers", type=int, default=1, metavar="N", help="Run --batch prompts in parallel with N workers")
    parser.add_argument("--fail-fast", action="store_true", help="With --batch: stop at the first failed prompt and exit non-zero")
    parser.add_argument("--max-output-chars", type=int, metavar="N", help="Cap tool output size (default from config, e.g. 60000)")
    parser.add_argument("--command-timeout", type=int, metavar="SECONDS", help="Per-command timeout for run_command (default from config)")
    parser.add_argument("--serve", action="store_true", help="Run a tiny HTTP API server (POST /chat, GET /health, GET /models)")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP server bind host (with --serve)")
    parser.add_argument("--port", type=int, default=8787, help="HTTP server port (with --serve; 0 = auto-assign)")
    parser.add_argument("--token", help="Require this bearer token for the HTTP API (with --serve; use a long random string)")
    parser.add_argument("--token-file", metavar="FILE", help="Read the bearer token from a file (with --serve)")
    parser.add_argument("--serve-background", action="store_true", help="Start the server detached in the background (with --serve)")
    parser.add_argument("--serve-pidfile", metavar="FILE", help="Pid file for the background server (default: ~/.termux-agent/server.pid)")
    parser.add_argument("--serve-stop", action="store_true", help="Stop a background server started with --serve --serve-background")
    parser.add_argument("prompt", nargs="*", help="One-shot prompt (no arguments = interactive mode)")
    parser.add_argument("--init", action="store_true", help="Create config.example -> ~/.termux-agent/config.yaml")
    parser.add_argument("--sessions", action="store_true", help="List saved sessions")
    parser.add_argument("--limit", type=int, default=20, metavar="N", help="Max sessions to list (with --sessions/--export-all)")
    parser.add_argument("--session-dir", metavar="DIR", help="Use this directory for session files instead of ~/.termux-agent/sessions")
    parser.add_argument("--search", help="Filter --sessions by keyword in the first message")
    parser.add_argument("--export", nargs="?", const="latest", metavar="SESSION", help="Print a session as portable JSON (default: latest); --markdown for a readable transcript")
    parser.add_argument("--markdown", action="store_true", help="With --export/--show, print a readable Markdown transcript")
    parser.add_argument("--import", dest="import_path", metavar="FILE", help="Import a portable session JSON file ('-' reads stdin; --dry-run validates only)")
    parser.add_argument("--prune", type=int, metavar="N", help="Delete all sessions except the newest N (--dry-run previews)")
    parser.add_argument("--prune-days", type=int, metavar="DAYS", help="Delete sessions older than this many days (--dry-run previews)")
    parser.add_argument("--keep", type=int, default=0, metavar="N", help="With --prune-days: keep the N newest sessions")
    parser.add_argument("--dry-run", action="store_true", help="With --prune/--prune-days: show what would be deleted without deleting")
    parser.add_argument("--redact", action="store_true", help="With --config-show: mask secrets in the output")
    parser.add_argument("--config-show", action="store_true", help="Print the effective merged configuration as YAML (--redact masks secrets)")
    parser.add_argument("--list-tools", action="store_true", help="List all registered tools")
    parser.add_argument("--forget", nargs="?", const="latest", metavar="SESSION", help="Delete one session (default: latest)")
    parser.add_argument("--export-all", metavar="DIR", help="Export every session as a JSON file into DIR (--markdown: readable .md transcripts)")
    parser.add_argument("--bench", nargs="?", const="__default__", metavar="PROVIDER", help="Benchmark latency across a provider's models (one tiny request each)")
    parser.add_argument("--list-providers", action="store_true", help="List provider presets")
    parser.add_argument("--list-agents", action="store_true", help="List available sub-agents")
    parser.add_argument("--models", nargs="?", const="__default__", metavar="PROVIDER", help="List models for a provider (live, or preset fallback)")
    parser.add_argument("--doctor", action="store_true", help="Diagnose environment & config; --doctor-termux also checks termux-api commands")
    parser.add_argument("--doctor-termux", action="store_true", help="With --doctor, check termux-api availability for notifications/clipboard/screenshots etc.")
    parser.add_argument("--doctor-network", action="store_true", help="Also check provider connectivity (needs internet)")
    parser.add_argument("--doctor-update", action="store_true", help="With --doctor, check the latest published version on PyPI")
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
        "--completion",
        nargs="?",
        const="bash",
        metavar="SHELL",
        help="Print the auto-completion script for bash/zsh to stdout (no install)",
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


def cmd_help_json() -> int:
    """Print the full CLI reference as machine-readable JSON (flags, help, defaults)."""
    import json as _json

    parser = build_parser()
    flags = []
    for a in parser._actions:
        if a.option_strings:
            flags.append(
                {
                    "flags": a.option_strings,
                    "dest": a.dest,
                    "metavar": a.metavar,
                    "help": (a.help or "").replace("\n", " "),
                    "default": a.default,
                    "type": getattr(a.type, "__name__", None),
                    "nargs": a.nargs,
                    "const": a.const,
                }
            )
    print(_json.dumps({"prog": "termux-agent", "description": "A CLI coding agent for Termux, like opencode.", "flags": flags}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.provider and ":" in args.provider and not args.model:
        args.provider, args.model = args.provider.split(":", 1)

    if args.help_json:
        return cmd_help_json()

    if args.version:
        import json as _json

        if args.json:
            print(_json.dumps({"name": "termux-agent", "version": __version__}))
        else:
            print(f"termux-agent {__version__}")
        return 0

    if args.no_color:
        from termux_agent.ui import renderer

        renderer.disable_color()

    if args.notify:
        from termux_agent.notify import notify_on_done

        notify_on_done(True)

    if args.session_dir:
        from termux_agent import session as sessionmod

        sessionmod.SESSIONS_DIR = Path(args.session_dir).expanduser()
        sessionmod.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        render_error(str(e))
        return 1

    if args.init:
        return cmd_init(args.provider, args.model)
    if args.bench:
        return cmd_bench(cfg, args.bench, args.timeout or 60, as_json=args.json)
    if args.export:
        return cmd_export(args.export, as_markdown=args.markdown)
    if args.export_all:
        return cmd_export_all(args.export_all, as_markdown=args.markdown, as_json=args.json)
    if args.forget:
        return cmd_forget(args.forget, as_json=args.json)
    if args.import_path:
        return cmd_import(args.import_path, dry_run=args.dry_run, as_json=args.json)
    if args.show:
        return cmd_show(args.show, as_json=args.json, output=args.output)
    if args.summarize:
        return cmd_summarize(
            cfg,
            args.summarize,
            args.provider,
            args.model,
            output=args.output,
            as_json=args.json,
            timeout=args.timeout,
            notify=args.notify,
        )
    if args.rerun:
        return cmd_rerun(
            cfg,
            args.rerun,
            args.provider,
            args.model,
            output=args.output,
            as_json=args.json,
            timeout=args.timeout,
            attach=args.attach,
            diff=args.diff,
            notify=args.notify,
        )
    if args.tokens is not None:
        return cmd_tokens(args.tokens, as_json=args.json)
    if args.bundle:
        return cmd_bundle(args.bundle)
    if args.restore:
        return cmd_restore(args.restore)
    if args.cron:
        if not prompt:
            render_error("--cron requires a one-shot prompt.")
            return 2
        return cmd_cron(args.cron, prompt, as_json=args.json)
    if args.cleanup:
        return cmd_cleanup()
    if args.prune is not None:
        return cmd_prune(args.prune, as_json=args.json, dry_run=args.dry_run)
    if args.prune_days is not None:
        return cmd_prune_days(args.prune_days, as_json=args.json, dry_run=args.dry_run, keep=args.keep)
    if args.config_show:
        return cmd_config_show(cfg, as_json=args.json, redact=args.redact)
    if args.list_tools:
        return cmd_list_tools()
    if args.sessions:
        return cmd_sessions(args.search, as_json=args.json, limit=args.limit)
    if args.show_system_prompt:
        return cmd_show_system_prompt(
            cfg,
            args.provider,
            args.model,
            agent_name=args.agent,
            working_dir=args.cwd,
            no_tools=args.chat,
            rules_file=args.rules,
            system_prompt_file=args.system_prompt_file,
            context=args.context,
            disabled_groups=_disabled_groups_from(args),
        )

    # Auto-create ~/.termux-agent/config.yaml on first run (like opencode).
    if not CONFIG_FILE.exists() and not args.config:
        ensure_config_file()
        render_info(
            f"First-run configuration created at {CONFIG_FILE} - edit it if needed, "
            "or just start using it (default: free OpenCode Zen)."
        )

    if args.doctor or args.doctor_network:
        return cmd_doctor(cfg, network=args.doctor_network, as_json=args.json, termux=args.doctor_termux, update=args.doctor_update)
    if args.smoke:
        return cmd_smoke(cfg, args.provider, args.model, as_json=args.json)
    if args.serve_stop:
        return cmd_serve_stop(args.serve_pidfile)
    if args.serve:
        token = args.token
        if not token and args.token_file:
            try:
                token = Path(args.token_file).expanduser().read_text(encoding="utf-8").strip()
            except OSError as e:
                render_error(f"Cannot read --token-file: {e}")
                return 1
        return cmd_serve(
            cfg,
            host=args.host,
            port=args.port,
            provider=args.provider,
            model=args.model,
            auto_accept=args.yes,
            token=token,
            background=args.serve_background,
            pidfile=args.serve_pidfile,
            log_file=args.log,
        )

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
        return cmd_list_providers(cfg, as_json=args.json)
    if args.list_agents:
        return cmd_list_agents(cfg, as_json=args.json)
    if args.models is not None:
        pname = None if args.models == "__default__" else args.models
        return cmd_list_models(cfg, pname, as_json=args.json)
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

    if args.completion:
        from termux_agent.completion import BASH_SCRIPT, ZSH_SCRIPT

        shell = args.completion.lower()
        if shell == "bash":
            print(BASH_SCRIPT, end="")
        elif shell == "zsh":
            print(ZSH_SCRIPT, end="")
        else:
            render_error("Unsupported shell (use bash or zsh).")
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
        if img.startswith(("http://", "https://")):
            import tempfile
            import urllib.parse
            import urllib.request

            try:
                with urllib.request.urlopen(img, timeout=30) as resp:
                    data = resp.read()
                ext = Path(urllib.parse.urlparse(img).path).suffix or ".jpg"
                tmp_img = Path(tempfile.gettempdir()) / f"termux-agent-img{ext}"
                tmp_img.write_bytes(data)
                img = str(tmp_img)
                render_info(f"Downloaded image to {img} ({len(data)} bytes).")
            except Exception as e:  # noqa: BLE001
                render_error(f"Failed to download image: {e}")
                return 1
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
            stream=args.stream and not args.no_stream,
            disabled_groups=_disabled_groups_from(args),
            max_output_chars=args.max_output_chars,
            command_timeout=args.command_timeout,
            allow_dirs=_allow_dirs_from(args),
        )

    if args.watch:
        if not prompt:
            render_error("--watch requires a one-shot prompt.")
            return 2
        return cmd_watch(
            cfg,
            prompt,
            args.provider,
            args.model,
            interval=args.watch,
            with_screenshot=args.screenshot,
            auto_accept=args.yes,
            timeout=args.timeout,
            disabled_groups=_disabled_groups_from(args),
            max_output_chars=args.max_output_chars,
            command_timeout=args.command_timeout,
            agent_name=args.agent,
            working_dir=args.cwd,
            only_tools=_split_tools(args.only_tools),
            context=args.context,
            allow_dirs=_allow_dirs_from(args),
            screenshot_dir=args.screenshot_dir,
            max_rounds=args.max_rounds,
            notify=args.notify,
            diff=args.diff,
            as_json=args.json,
            output=args.output,
            exit_on_change=args.exit_on_change,
        )

    if args.batch:
        return cmd_batch(
            cfg,
            args.batch,
            args.provider,
            args.model,
            output=args.output,
            auto_accept=args.yes,
            timeout=args.timeout,
            as_json=args.json,
            disabled_groups=_disabled_groups_from(args),
            max_output_chars=args.max_output_chars,
            command_timeout=args.command_timeout,
            agent_name=args.agent,
            working_dir=args.cwd,
            only_tools=_split_tools(args.only_tools),
            workers=args.workers,
            allow_dirs=_allow_dirs_from(args),
            fail_fast=args.fail_fast,
            notify=args.notify,
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
            stream=args.stream and not args.no_stream,
            retries=args.retries,
            no_fallback=args.no_fallback,
            rules_file=args.rules,
            system_prompt_file=args.system_prompt_file,
            context=args.context,
            disabled_groups=_disabled_groups_from(args),
            max_output_chars=args.max_output_chars,
            command_timeout=args.command_timeout,
            no_save=args.no_save,
            git_context=args.git_context,
            only_tools=_split_tools(args.only_tools),
            log_file=args.log,
            memory=not args.no_memory,
            allow_dirs=_allow_dirs_from(args),
            screenshot_dir=args.screenshot_dir,
            attach=args.attach,
            rotate=args.rotate,
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
            log_file=args.log,
        ).run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())