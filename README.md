# termux-agent

> A CLI coding agent for **Termux (Android)** — chat with an LLM that can read/write files, search code, and run commands through a full tool-use loop. Multi-provider, scriptable, and works out of the box with a free model.

[![Version](https://img.shields.io/badge/version-1.2.0-blue)](#)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](#)
[![Python](https://img.shields.io/badge/python-3.10%2B-informational)](#)
[![Tests](https://img.shields.io/badge/tests-353%20passing-brightgreen)](#)
[![CI](https://github.com/aixwim/termux-agent/actions/workflows/ci.yml/badge.svg)](#)

---

## Quick start

```bash
# 1. install Python + git
pkg update && pkg install -y python git

# 2. install termux-agent (creates the `termux-agent` command)
git clone https://github.com/aixwim/termux-agent.git
cd termux-agent && pip install .

# 3. run it — no configuration needed, a free model is used by default
termux-agent                              # interactive mode
termux-agent "explain this repo"          # one-shot mode
```

That's it. The default provider is [OpenCode Zen](https://opencode.ai) — its free models work with no API key. For other providers, set an API key (see [Providers](#providers--api-keys)).

`bash scripts/install-termux.sh` performs the same steps non-interactively.

---

## Highlights

- **Works immediately** — default provider needs no API key.
- **Real agent loop** — the model answers or calls tools (`read_file`, `write_file`, `edit_file`, `grep_file`, `run_command`, `web_search`, `git_commit`, …) until the task is done.
- **Sub-agents** — role-restricted personalities: `root` (everything), `explore` (read-only), `coder` (no commits), `shell` (commands only).
- **Safe by default** — working-directory boundary, command confirmation, timeouts, output truncation, and a `--readonly` mode.
- **Session store** — every conversation is saved, resumable, searchable, note-tagged, exportable, and fully backup-able.
- **Built-in HTTP API** — OpenAI-compatible, for Tasker, Termux:API, scripts, or web clients.
- **Battery-friendly — built for phones** — Termux notifications, wake locks, text-to-speech, screenshots, clipboard, and device context.

---

## Table of contents

- [Features](#features)
- [Install on Termux](#install-on-termux)
- [Usage](#usage)
- [Configuration](#configuration)
- [Providers & API keys](#providers--api-keys)
- [HTTP API](#http-api)
- [Development](#development)
- [License](#license)

---

## Features

### Chat & agents

- Interactive REPL with multi-line input (`{{ ... }}`), tab completion, and streaming output.
- One-shot mode for scripts and pipelines: `termux-agent "prompt"` or `echo ... | termux-agent`.
- Sub-agents with per-agent tool restrictions (`--agent` / `/agent`).
- Plan-first mode (`--plan`): propose, approve, then execute.
- Read-only mode (`--readonly`) and plain-chat mode (`--chat`).
- `/prompt` session instructions, custom personas via `--system-prompt`, and project rules from `AGENTS.md` / `CLAUDE.md`.

### Tools

| Group | Tools |
|---|---|
| Files | `read_file`, `write_file`, `edit_file`, `list_dir` |
| Search | `grep_file`, `glob_find` |
| Shell | `run_command` (with confirmation) |
| Web | `web_fetch`, `web_search` (DuckDuckGo + Wikipedia fallback) |
| Git | `git_status`, `git_diff`, `git_log`, `git_commit` |

Restrict any group with `--no-shell`, `--no-web`, `--no-git`, or pin exact tools with `--only-tools`.

### Sessions

- Auto-saved transcripts with `--resume` / `/resume`.
- Search, filter, and tag sessions: `--search-sessions`, `--sessions --search`, `--note`, `--sessions --notes-only`.
- Review, summarize, re-run, and diff sessions: `--show`, `--summarize`, `--rerun`.
- Token estimation for files, directories, git diffs, or the whole store (`--tokens`).
- Portable backup and restore (`--bundle` / `--restore`), export (`--export` / `--export-all`), and import (`--import`).

### Providers

- OpenAI, Anthropic, OpenRouter, Ollama, Groq, DeepSeek, Gemini, xAI (Grok), Mistral, Cerebras, and OpenCode Zen.
- Automatic fallback models on rate limits, transient retries for flaky networks, model rotation (`--rotate`), and a latency benchmark (`--bench`).

### Termux / device integration

- Notifications (`--notify`), wake locks (`--wakelock`), text-to-speech (`--speak`).
- Screen capture (`--screenshot`), clipboard I/O (`--clip` / `--copy`), battery/wifi/time context (`--context`).
- Android storage access (`allow_storage` + `termux-setup-storage`).

### Scripting & automation

- `--json` structured output everywhere, `--quiet` for plain answers, `--output FILE` to save results.
- Batch runs with parallel workers (`--batch --workers N`), cron scheduling (`--cron`), and shell completions (`--completion bash|zsh|fish`).

---

## Install on Termux

```bash
# optional, for Android file access
termux-setup-storage

# install Python + git
pkg update && pkg install -y python git

# clone, install, and run
cd ~
git clone https://github.com/aixwim/termux-agent.git
cd termux-agent
pip install .
termux-agent
```

On first run, `~/.termux-agent/config.yaml` is created automatically with a free default model — you can ignore it. To use a paid provider, export its API key:

```bash
export OPENAI_API_KEY=sk-...   # or ANTHROPIC_API_KEY, GROQ_API_KEY, OPENCODE_API_KEY, ...
```

### Updating

```bash
cd ~/termux-agent && git pull && pip install . --force-reinstall --no-deps
```

---

## Usage

### Interactive

```bash
termux-agent                          # start the REPL
```

| Command | Description |
|---|---|
| `/provider NAME` | switch provider (`/provider zen:model-name`) |
| `/model MODEL` | switch model |
| `/resume [ID]` | resume a session (default: latest) |
| `/sessions` | list saved sessions |
| `/search TERM` | find sessions containing a term |
| `/note [TXT]` | attach/read a note to the current session |
| `/notes` | list notes across all sessions |
| `/tokens` | estimate the current conversation's tokens |
| `/session` / `/last` | show session id / re-print the last answer |
| `/export [PATH]` | export as Markdown (`/export json` for JSON) |
| `/redo` / `/retry` | re-run the last turn (redo uses the current model) |
| `/compact` | summarize old history to save context |
| `/memory` / `/remember` | show/add persistent memory |
| `/undo` | revert the most recent file write/edit |
| `/plan`, `/temp`, `/maxrounds`, `/bench`, `/context`, `/image`, `/attach` | tuning & input helpers |
| `/help` | full command reference |
| `/exit` | quit |

### One-shot

```bash
termux-agent "read main.py and fix its bugs"     # one-shot
echo "fix the bugs in main.py" | termux-agent     # pipe stdin as the prompt
termux-agent --json "summarize this repo"         # machine-readable result
termux-agent --quiet "what files changed?"        # answer only (no banner)
termux-agent --notify "run the test suite"        # notify when done
```

### Sessions & review

```bash
termux-agent --sessions                            # list sessions
termux-agent --sessions --search "calculator"      # filter by keyword
termux-agent --show                                # latest transcript
termux-agent --show 20260820-000001 --output out.md
termux-agent --summarize 20260820-000001 --json    # distil a session
termux-agent --rerun 20260820-000001 --diff        # re-run + diff vs old answer
termux-agent --tokens --all                        # token estimate for the whole store
termux-agent --search-sessions "ssh"               # grep every transcript + note
termux-agent --forget 20260820-000001              # delete one
termux-agent --forget --all --dry-run              # preview wiping everything
termux-agent --bundle ./backup                     # backup config+memory+sessions
termux-agent --restore ./backup --merge            # restore without overwriting
```

### Batch

```bash
printf "summarize main.py\ncheck for bugs in utils.py\n" > tasks.txt
termux-agent --batch tasks.txt --output results.json   # one prompt per line
termux-agent --batch tasks.txt --workers 4             # parallel
termux-agent --batch ./prompts-dir                     # every .txt/.md file in a dir
termux-agent --batch tasks.txt --notify --fail-fast    # notify + stop on first error
```

### Watch

```bash
termux-agent --watch 30 "what changed on my screen?"   # re-run every 30s
termux-agent --watch 30 --screenshot --context          # with screen capture
termux-agent --watch 30 --diff                          # only print changed rounds
termux-agent --watch 30 --exit-on-change               # stop when the answer changes
termux-agent --watch 30 --json                          # one JSON line per round
```

### Diagnostics & tuning

```bash
termux-agent --doctor            # full environment check
termux-agent --doctor --fix      # repair common issues automatically
termux-agent --health            # fast offline health check
termux-agent --bench zen         # latency per model (--bench --all for all providers)
termux-agent --smoke             # end-to-end test with the real model
termux-agent --tokens main.py    # estimate before a big prompt
```

---

## Configuration

The config file lives at `~/.termux-agent/config.yaml` (or project-local `.termux-agent/config.yaml`, which overrides it per project). Manage it from the CLI:

```bash
termux-agent --config-show                    # print the effective config
termux-agent --config-set temperature 0.2      # change a value
termux-agent --config-set providers.zen.model nemotron-3-ultra-free
termux-agent --config-unset temperature        # remove a key
```

Key options (see [config.example.yaml](config.example.yaml) for the full annotated file):

| Key | Default | Purpose |
|---|---|---|
| `provider` / `model` | `zen` | default provider and model |
| `temperature` | `0.7` | sampling temperature |
| `max_tool_rounds` | `20` | tool-call loop cap |
| `max_context_tokens` | `0` | auto-compact history past this budget |
| `retries` / `retry_backoff` | `1` / `1.0` | transient-failure retries |
| `whitelisted_commands` | `[]` | commands that skip confirmation |
| `working_dir` | `~` | file/command access boundary |
| `allow_storage` | `false` | Android shared storage access |
| `notify_on_done` | `false` | Termux notification on completion |

---

## Providers & API keys

| Provider | Env var | Notes |
|---|---|---|
| `zen` | `OPENCODE_API_KEY` (optional) | OpenCode Zen — free models work with no key |
| `openai` | `OPENAI_API_KEY` | OpenAI GPT |
| `anthropic` | `ANTHROPIC_API_KEY` | Claude (Messages API) |
| `openrouter` | `OPENROUTER_API_KEY` | many models via one key |
| `groq` | `GROQ_API_KEY` | fast, cheap models |
| `deepseek` | `DEEPSEEK_API_KEY` | DeepSeek chat/reasoner |
| `gemini` | `GEMINI_API_KEY` | Google Gemini (OpenAI-compatible endpoint) |
| `ollama` | — | local models on-device (`pkg install ollama`) |

### Free OpenCode Zen models

```bash
termux-agent --provider zen --model nemotron-3-ultra-free "what is 2+2?"
```

Currently free: `nemotron-3-ultra-free`, `deepseek-v4-flash-free`, `mimo-v2.5-free`, `big-pickle`. Other models require an `OPENCODE_API_KEY` from <https://opencode.ai/auth>.

---

## HTTP API

Start a local server with `--serve` (or run it detached with `--serve-background`):

```bash
termux-agent --serve --port 8787 --token "your-token"
curl http://127.0.0.1:8787/health
curl -X POST http://127.0.0.1:8787/chat \
  -H 'Authorization: Bearer your-token' -H 'Content-Type: application/json' \
  -d '{"prompt":"hello","stream":true}'
```

| Endpoint | Description |
|---|---|
| `GET /health` | version, pid, uptime |
| `GET /models?provider=X` | provider's models |
| `GET /sessions[?note=TERM&limit=N]` | saved sessions (filterable by note) |
| `GET /sessions/<id>[?markdown=1]` | full transcript or Markdown |
| `POST /sessions/<id>/note` | attach/update a session note |
| `DELETE /sessions/<id>` | delete a session |
| `POST /chat` | chat with the agent (all CLI overrides + `note`, `stream`, `history`, `session`) |
| `POST /batch` | run a list of prompts in parallel |
| `POST /summarize` | summarize a stored session (`{"session": id}`) |
| `POST /rerun` | re-run a session's last question (`{"session": id}`) |
| `POST /memory` / `GET /memory` | persistent memory |
| `POST /v1/chat/completions` | OpenAI-compatible endpoint (works with OpenAI SDK clients) |

---

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
python tests/mock_server.py &   # mock provider for offline testing
termux-agent --model mock-model "..."   # use the mock provider
```

```
termux_agent/
├── cli.py          # CLI entry point + commands
├── config.py       # configuration + provider presets
├── agent.py        # agent loop + tool-call
├── session.py      # JSONL session storage + notes
├── providers/      # base, openai_compat, anthropic
├── tools/          # files, search, shell, web + registry
└── ui/             # renderer (rich) + REPL (prompt_toolkit)
```

CI runs the full test suite on Python 3.10–3.14 via GitHub Actions.

---

## License

MIT — see [LICENSE](LICENSE).
