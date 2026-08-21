# Changelog

All notable changes to this project are documented here.

## Unreleased

- Bound local and remote vision inputs to 10 MiB, verify image signatures, use collision-safe temporary files, and clean downloads after each command.
- Make batch, watch, aggregate summarize, and aggregate rerun status/exit codes reflect partial and total failures accurately.
- Report exhausted provider/model failures as real workflow failures across one-shot, batch, watch, summarize, rerun, and resume.
- Preserve valid machine-readable output across one-shot and dispatcher preflight failures in `--json` workflows.
- Share quoted-path, URL, safe-decoding, binary rejection, and 2 MiB-per-file/4 MiB-total memory protection across all attachment modes.
- Add `/history [N]` for a safe compact conversation view and `/clear` for resetting the terminal viewport without losing context.
- Add searchable `/help [TERM]` output and typo-aware suggestions for unknown REPL commands.
- Add a live REPL status bar for provider/model, agent, mode, message count, and help discovery.
- Add slash-command completion, history-based inline suggestions, and dynamic completion for paths, sessions, providers, models, agents, and common tuning values.
- Add a `/status` REPL dashboard for active configuration, session state, token counts, and last-run diagnostics.
- Make shell-command confirmation visually distinct, default-safe, and EOF-safe in the interactive REPL.
- Present interactive configuration, session usage, recent sessions, and search results as aligned, phone-friendly summaries and safe tables.
- Refresh the interactive terminal theme with clearer answer panels, user/assistant identity, semantic status icons, compact tool activity, a scannable `/help` table, a TTY-only thinking indicator, and a more informative mobile-friendly banner.
- Add a Termux-compatible Flake8 gate for critical Python errors to local development and CI.
- Add a least-privilege CI dependency audit using `pip-audit`.
- Fix `--cron` using its prompt before the CLI had initialized it.
- Add JSON run diagnostics for elapsed time, model attempts, retries, and fallbacks.
- Add time-to-first-token, agent round, and tool-call metrics to JSON and `--stats` output.

## [1.2.0] - 2026-08-20

### Highlights

- **Faster, lower-memory search** — file traversal, globbing, and session metadata are streamed lazily; generated directories are pruned on large repositories.
- **Polished mobile output** — compact adaptive banner, clearer doctor sections, concise tool-call lines, and correct multiline streaming on narrow Termux screens.
- **More reliable model fallback** — empty provider responses are retried and then routed to a fallback model instead of being reported as successful blank answers.
- **Safer read-only inspection** — `git_log` is available in read-only mode.
- **Accurate diagnostics** — provider HTTP responses are distinguished from network failures, and unpublished PyPI packages are no longer reported as offline.
- **Python 3.14 support** — included in package classifiers and the CI test matrix.

### Fixes

- Piped REPL slash-commands now reach the REPL correctly.
- Unknown providers return a friendly error instead of a traceback.
- `--doctor --fix` is accepted alongside `--doctor-fix`.
- `--tokens --all` no longer consumes `--all` as a file argument.
- Rate-limit and API errors are visible in the REPL.
- Zen fallback models prefer currently available free models.

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
