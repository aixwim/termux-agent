# Changelog

All notable changes to this project are documented here.

### Fixes

- **Piped REPL commands now work** — piping slash-commands (`printf '/sessions\n' | termux-agent`) used to be swallowed as a one-shot prompt; they now reach the REPL correctly.
- **Friendly error for unknown providers** — no more raw traceback for `--provider nope`.
- **`--doctor --fix` works** — the intuitive `--fix` alias is now accepted alongside `--doctor-fix`.

- **`--tokens --all` works** — the `--tokens` flag no longer swallows `--all` as its file argument; whole-store estimation works as documented.

## [1.1.0] - 2026-08-20

### Optimization for Termux

- **Much faster startup** — lazy-loaded heavy imports (`prompt_toolkit`, `httpx`, `rich`, `yaml`, `urllib`). `--version` went from ~3.8s to ~0.8s on-device.
- **Narrow-terminal aware rendering** — automatically switches to plain output on dumb/small terminals (phone screens, piped automation) instead of slow ANSI/markdown rendering.
- **Fast session listing** — `--sessions` reads only the metadata lines of each session file instead of decoding every record.
- **Mobile-friendly default** — `retries` bumped to 2 for flaky cellular networks.
- **Storage detection in `--doctor`** — suggests enabling `allow_storage` when Android storage is detected.

## [1.0.0] - 2026-08-20

Initial public release.

### Highlights

- **Agentic coding assistant for Termux** with a full tool-use loop (files, search, shell, web, git).
- **Works out of the box** using a free OpenCode Zen model — no API key required.
- **11+ providers** including OpenAI, Anthropic, Groq, Gemini, Ollama, and OpenCode Zen.
- **Interactive REPL and one-shot modes**, sub-agents, plan-first mode, and read-only mode.
- **Persistent session store** with resume, search, notes, export/import, and full backup/restore.
- **Built-in HTTP API** with an OpenAI-compatible endpoint and streaming (SSE) support.
- **Termux-native**: notifications, wake locks, TTS, screenshots, clipboard, and device context.

### Commands

- Core: `--chat`, `--plan`, `--readonly`, `--agent`, `--resume`, `--watch`, `--batch`, `--serve`.
- Review: `--show`, `--summarize`, `--rerun`, `--tokens`, `--export`, `--export-all`, `--import`.
- Housekeeping: `--sessions`, `--note`, `--search-sessions`, `--prune`, `--forget`, `--bundle`, `--restore`, `--stats-all`, `--cleanup`.
- Diagnostics: `--doctor`, `--health`, `--bench`, `--smoke`, `--list-tools`, `--list-agents`, `--models`.
- Scripting: `--json`, `--quiet`, `--output`, `--cron`, `--completion`, `--log`.

### Reliability

- Transient-failure retries, fallback models on rate limits, model rotation, and strict timeouts.
- Session storage is JSONL — easy to inspect, parse, or migrate by hand.