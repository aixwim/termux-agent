# Changelog

All notable changes to this project are documented here.

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