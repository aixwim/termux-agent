# termux-agent

A CLI coding agent for **Termux (Android)**, similar to [opencode](https://opencode.ai): chat with an LLM + a tool-use loop for reading/writing files, searching code, and running commands — with **multi-provider** support.

## Features

- **Works right away**: after installing, just type `termux-agent` — the default uses a free OpenCode Zen model (no API key needed), just like opencode is usable immediately.
- **Full agent loop**: the model can answer or call tools (`read_file`, `write_file`, `edit_file`, `list_dir`, `grep_file`, `glob_find`, `run_command`, `web_fetch`, `web_search`, `git_status`, `git_diff`, `git_commit`) until the task is done.
- **Multi-agent / sub-agents**: specialized agents with different roles & tool restrictions — `root` (all tools), `explore` (read/search only), `coder` (write/edit without commits), `shell` (run commands). Pick one with `--agent` or `/agent`.
- **Read-only mode (`--readonly`)**: the agent can only read, search, and browse the web — no writing/editing/running commands. Great for reviewing code without risk.
- **Plan mode (`--plan`)**: the agent first proposes a step-by-step plan read-only, then executes it only after you approve (auto-approved with `--yes`).
- **Token usage tracking**: `/stats` shows prompt/completion/total tokens used in the session (when the provider reports usage).
- **Auto context compacting**: `--max-context-tokens N` (or `max_context_tokens` in config) summarizes old history automatically once the session passes N cumulative tokens.
- **Session management**: `/sessions`, `/forget [ID]` (delete a session), and `/config` (show the active configuration).
- **Command whitelist**: `whitelisted_commands` in config lists extra command prefixes that skip confirmation (e.g. `["pip install", "python app.py"]`).
- **Termux notifications**: `--notify` (or `notify_on_done: true` in config) sends a `termux-notification` when a one-shot task finishes (needs termux-api).
- **Wake lock & TTS**: `--wakelock` holds a Termux wake lock while a long task runs (prevents CPU sleep), and `--speak` reads the answer aloud with `termux-tts-speak` (both need termux-api).
- **HTTP API**: `--serve` runs a tiny local server (`POST /chat`, `GET /health`, `GET /models`, `GET /sessions`, with CORS enabled for browser clients). Every request is saved as a session and the id is returned in the response; pass `"session": "<id>"` to resume that conversation. Use `--token` to require a bearer token on all endpoints except `/health`.
- **Chat mode**: `--chat` disables all tools for a plain conversation (no file/command access).
- **Timeouts & saving**: `--timeout SECONDS` aborts a slow one-shot task (exit 124); one-shot tasks are now saved as sessions too, so you can `--resume` them.
- **Session backup**: `--export [ID]` prints a session as portable JSON (redirect to a file), `--export-all DIR` backs up every session, `--import FILE` restores one, `--prune N` / `--forget [ID]` delete sessions. `--bench [PROVIDER]` times one tiny request per model to help you pick a fast default.
- **Phone-native input**: `--clip` reads the prompt from the clipboard, `--screenshot` captures the screen with `termux-screenshot` and attaches it as an image (both need termux-api). Auto-completion (`--install-completion`) derives flags from `--help`, so it never goes stale.
- **Streaming**: `--stream` prints the answer as it is generated; `--prompt-file -` reads the prompt from stdin. In the REPL, `/plan` toggles plan-first mode (read-only plan, then approve before execution).
- **Scriptable resumes**: `--resume` supports `--json` and `--quiet` for automated continuation; `--sessions --search "keyword"` filters sessions.
- **Scripting output**: `--json` (structured result) and `--quiet` (only the answer, no banner) for pipelines; `--copy` sends the answer to the clipboard; `--stats` prints token usage after the answer.
- **Pipe-friendly**: `echo "fix the bug" | termux-agent` runs a one-shot using stdin as the prompt.
- **Rate-limit fallback**: `fallback_models` in the provider config are tried automatically when the main model returns HTTP 429 (rate limited).
- **Flaky-network retries**: transient failures (network blips, HTTP 5xx) are retried automatically (`retries`, `retry_backoff` in config) — handy on mobile connections.
- **Persistent memory**: `/remember <text>` appends notes to `~/.termux-agent/memory.md`, which is loaded into every new session's instructions.
- **Undo file changes**: `/undo` in interactive mode restores the most recent file write/edit (the agent keeps a snapshot of every changed file).
- **JSON output**: `termux-agent --json "prompt"` prints a machine-readable result `{ok, answer, tool_calls, usage, provider, model}`.
- **Web search without an API key**: `web_search` uses DuckDuckGo, with a Wikipedia fallback when the network blocks/rejects certificates.
- **Image / vision input**: `--image photo.jpg` or the inline marker `[image: path]` in a prompt attach a picture (e.g. a screenshot) for vision-capable models.
- **Auto-completion**: `--install-completion bash|zsh` adds Tab-completion to your shell (providers, agents, and CLI options).
- **Diagnostics**: `--doctor` checks the Termux environment, config, PATH, and API key; `--doctor-network` also tests provider connectivity; `--smoke` sends a tiny real prompt end-to-end to verify the whole pipeline.
- **Project rules**: `AGENTS.md`, `CLAUDE.md`, or `.termux-agent/rules.md` in the working directory (and parents up to `$HOME`) are auto-loaded into the agent's instructions — like opencode.
- **Project-local config**: a `.termux-agent/config.yaml` in the current directory (or any parent up to `$HOME`) overrides the global `~/.termux-agent/config.yaml` for that project.
- **Session instructions**: `/prompt <text>` adds a persistent instruction for the rest of the session (`/prompt` shows them, `/prompt clear` removes them).
- **Git integration**: the agent can check status, diff, recent history, and commit (commit requires confirmation).
- **Session resume**: continue a previous conversation with `--resume` or `/resume`.
- **Context compacting**: `/compact` summarizes old history to save tokens.
- **Automation mode**: `--yes` skips all confirmations (good for scripts).
- **Android storage access**: enable `allow_storage: true` in config to access files in `/storage/emulated/0` (run `termux-setup-storage` first).
- **Multi-provider** presets: OpenAI, Anthropic, OpenRouter, Ollama, Groq, DeepSeek, Gemini, xAI (Grok), Mistral, Cerebras, and **OpenCode Zen**.
- **Multi-line input**: wrap a message in `{{ ... }}` in interactive mode to send several lines (e.g. paste code).
- **Interactive & one-shot modes**.
- **Sessions saved** to `~/.termux-agent/sessions/`.
- **Mobile-friendly safety**: working directory boundary, confirmation for non-whitelisted commands, timeouts, and output truncation to save memory.

## Install on Termux

```bash
# 1. prepare storage (optional, for Android file access)
termux-setup-storage

# 2. install Python + git
pkg update && pkg install -y python git

# 3. clone & install (creates the `termux-agent` command)
cd ~
git clone https://github.com/ANDA/termux-agent.git
cd termux-agent
pip install .

# 4. use it — works right away, no configuration needed
termux-agent                       # interactive mode
```

On the first run, `~/.termux-agent/config.yaml` is created automatically; you can ignore it because the default already uses a free model. For paid providers, just set the API key in the environment:

```bash
export OPENAI_API_KEY=sk-...        # or ANTHROPIC_API_KEY, GROQ_API_KEY, OPENCODE_API_KEY, etc.
```

A one-command script is also available: `bash scripts/install-termux.sh`.

## Usage

```bash
# interactive mode (default provider from config)
termux-agent

# one-shot mode
termux-agent "read file main.py and fix its bugs"

# resume the latest session
termux-agent --resume
termux-agent --resume 20260819-233713 "continue: ..."

# resume with machine-readable output (for scripts)
termux-agent --resume --json "continue the task" > result.json

# plain chat without any tools
termux-agent --chat "explain how TCP works"

# find a session by keyword
termux-agent --sessions --search "calculator"

# skip all confirmations (dangerous; good for scripts)
termux-agent --yes "commit all changes"

# choose provider/model
termux-agent --provider zen --model nemotron-3-ultra-free "check the contents of this directory"

# use a specialized sub-agent
termux-agent --agent explore "find where the main function is defined"  # read-only
termux-agent --agent coder "add unit tests for kalkulator.py"
termux-agent --list-agents   # list sub-agents

# list provider presets
termux-agent --list-providers

# list models for a provider (live, or preset fallback)
termux-agent --models
termux-agent --models zen

# interactive first-time setup (provider + model + API key hint)
termux-agent --init

# install Tab-completion (bash/zsh) once
termux-agent --install-completion bash
source ~/.bashrc

# diagnose environment & config
termux-agent --doctor
termux-agent --doctor-network      # also check provider connectivity

# quick overrides without editing config
termux-agent --cwd /sdcard/Documents --temperature 0.2 --max-tool-rounds 30 "tidy up this project"

# auto-summarize history once a session passes ~60k tokens
termux-agent --max-context-tokens 60000

# read-only mode: review code without being able to change anything
termux-agent --readonly "review the security of this code and suggest fixes"

# plan mode: propose a plan first, execute only after approval
termux-agent --plan "add unit tests for kalkulator.py"

# plan mode with machine-readable output (for scripts)
termux-agent --plan --yes --json "add unit tests for kalkulator.py"

# end-to-end smoke test with the real model
termux-agent --smoke

# pipe stdin as the prompt (one-shot)
echo "fix the bugs in main.py" | termux-agent

# machine-readable one-shot output (JSON for scripts)
termux-agent --json "summarize this repo" > result.json

# print only the answer (no banner/tool logs) - good for pipelines
termux-agent --quiet "what files changed?"

# copy the answer to the clipboard (needs termux-api)
termux-agent --copy "fix the bugs in main.py"

# attach an image (e.g. a screenshot) for vision-capable models
termux-agent --image /sdcard/DCIM/screenshot.png "explain what this screen shows"

# read the prompt from a file
termux-agent --prompt-file review.txt --json

# pass the API key for a single run (never saved)
termux-agent --provider xai --api-key sk-... --model grok-3 "explain this repo"

# print token usage after a one-shot answer
termux-agent --stats "summarize main.py"

# notify when a long one-shot task finishes (needs termux-api)
termux-agent --notify "update all dependencies"

# keep the CPU awake during a long task, then read the answer aloud
termux-agent --wakelock --speak "summarize this repo and read the summary"

# abort if a task takes longer than 5 minutes (exit code 124 on timeout)
termux-agent --timeout 300 "run the full test suite and fix failures"

# run a local HTTP API (for Tasker / Termux:API / scripts)
termux-agent --serve --host 127.0.0.1 --port 8787
curl -X POST http://127.0.0.1:8787/chat -d '{"prompt":"hello"}' -H 'Content-Type: application/json'
# every request is saved; pass the returned "session" id to resume it:
curl -X POST http://127.0.0.1:8787/chat -d '{"prompt":"continue","session":"20260819-..."}' -H 'Content-Type: application/json'

# protect the API with a bearer token (needed for any non-/health request)
termux-agent --serve --token "a-long-random-string"
curl -X POST http://127.0.0.1:8787/chat -d '{"prompt":"hello"}' -H 'Authorization: Bearer a-long-random-string'
curl http://127.0.0.1:8787/sessions -H 'Authorization: Bearer a-long-random-string'   # list saved sessions

# list sessions
termux-agent --sessions

# backup / move a session to another device (portable JSON on stdout)
termux-agent --export > backup.json
termux-agent --export 20260819-233713 > that-session.json
termux-agent --import that-session.json   # restore it as a session

# clean up old sessions, keeping only the newest 20
termux-agent --prune 20
termux-agent --forget 20260819-233713   # or delete just one session

# backup everything
termux-agent --export-all ./backup

# pick the fastest model on your connection (one tiny request per model)
termux-agent --bench zen

# save a one-shot answer to a file too (works with --quiet / --json)
termux-agent --quiet --output out.txt "summarize this repo"

# use the clipboard as the prompt (handy paired with --copy)
termux-agent --clip --copy "improve my clipboard text"

# attach a screenshot of the screen to the prompt (needs screen-share permission)
termux-agent --screenshot "what error is on my screen?"

# stream the answer to the terminal as it is generated
termux-agent --stream "explain this error"

# read the prompt from stdin
cat notes.txt | termux-agent --prompt-file -

# regenerate auto-completion (derives all flags from --help, never goes stale)
termux-agent --install-completion
```

### Project rules

Put `AGENTS.md` (or `CLAUDE.md`, `.termux-agent/rules.md`) in the working directory — its contents automatically become instructions for the agent:

```bash
echo "Always write a docstring in every function." > AGENTS.md
termux-agent "add docstrings to app.py"   # follows that rule
```

### Interactive mode commands

```
/exit, /quit   quit
/new           start a new session
/provider NAME switch provider (e.g. /provider ollama)
/model MODEL   switch model
/help          show help
/cwd           show the working directory
/sessions      list sessions
/resume [ID]   resume a session (ID optional, default: latest)
/compact       summarize session history to save context
/agent [NAME]  view/switch sub-agent (explore, coder, shell, ...)
/export [PATH] export the conversation to Markdown (default: ~/.termux-agent/exports/)
/copy          copy the last answer to the clipboard (requires termux-api)
/stats         show token usage of this session
/undo          revert the most recent file write/edit
/config        show the active configuration
/forget [ID]   delete a session (default: this session)
/models        list available models for the current provider
/diff          show git working-tree changes & diff summary
/prompt [TXT]  add a session instruction; /prompt clear removes them; no arg = show
/remember TXT  store a note in ~/.termux-agent/memory.md (loaded every session)
/cd DIR        change the working directory (and file-access boundary)
/plan          toggle plan-first mode (propose, approve, then execute)
Type a normal message to ask; Ctrl+C to cancel.
```

You can also define your own sub-agents in `~/.termux-agent/config.yaml`:

```yaml
agents:
  myhelper:
    description: "My custom assistant"
    prompt: "You only help with shell questions."
    tools: [run_command, read_file]
```

### Android file access (storage)

To let the agent read/write files in Android storage (photos, documents, etc.):

```bash
termux-setup-storage          # once: creates ~/storage -> /storage/emulated/0
# then enable it in ~/.termux-agent/config.yaml:
#   allow_storage: true
termux-agent "read the latest file in the Download folder"
```

## Providers & API keys

| Provider   | Env var               | Notes                                   |
|------------|-----------------------|-----------------------------------------|
| openai     | `OPENAI_API_KEY`      | OpenAI GPT                              |
| anthropic  | `ANTHROPIC_API_KEY`   | Claude (Messages API)                   |
| openrouter | `OPENROUTER_API_KEY`  | Many models via one key                 |
| ollama     | (no key)              | Local models on the device (`pkg install ollama`) |
| groq       | `GROQ_API_KEY`        | Fast & cheap models                     |
| deepseek   | `DEEPSEEK_API_KEY`    | DeepSeek chat/reasoner                  |
| gemini     | `GEMINI_API_KEY`      | Google Gemini (OpenAI-compat endpoint)  |
| zen        | `OPENCODE_API_KEY` (optional) | OpenCode Zen — free models work without a key |

### Free OpenCode Zen models

Without an API key, use a `*free` model:

```bash
termux-agent --provider zen --model nemotron-3-ultra-free "what is 2+2?"
```

Currently available free models: `nemotron-3-ultra-free`, `deepseek-v4-flash-free`, `mimo-v2.5-free`, `big-pickle`. Other models require `OPENCODE_API_KEY` from <https://opencode.ai/auth>.

## Development

```bash
pip install -e ".[dev]"          # dev dependencies (pytest)
python -m pytest tests/ -q        # 56 tests: tools, providers, agent, git, rules, resume
python tests/mock_server.py &     # mock OpenAI/Anthropic server (port 8765)
termux-agent --model mock-model "..."   # test without an API key
```

Structure:

```
termux_agent/
├── cli.py          # CLI entry point
├── config.py       # configuration + provider presets
├── agent.py        # agent loop + tool-call
├── session.py      # JSONL session storage
├── providers/      # base, openai_compat, anthropic
├── tools/          # files, search, shell, web + registry
└── ui/             # renderer (rich) + REPL (prompt_toolkit)
```

## License

MIT — see [LICENSE](LICENSE).