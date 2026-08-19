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
- **Pipe-friendly**: `echo "fix the bug" | termux-agent` runs a one-shot using stdin as the prompt.
- **Rate-limit fallback**: `fallback_models` in the provider config are tried automatically when the main model returns HTTP 429 (rate limited).
- **Undo file changes**: `/undo` in interactive mode restores the most recent file write/edit (the agent keeps a snapshot of every changed file).
- **JSON output**: `termux-agent --json "prompt"` prints a machine-readable result `{ok, answer, tool_calls, usage, provider, model}`.
- **Web search without an API key**: `web_search` uses DuckDuckGo, with a Wikipedia fallback when the network blocks/rejects certificates.
- **Auto-completion**: `--install-completion bash|zsh` adds Tab-completion to your shell (providers, agents, and CLI options).
- **Diagnostics**: `--doctor` checks the Termux environment, config, PATH, and API key; `--doctor-network` also tests provider connectivity.
- **Project rules**: `AGENTS.md`, `CLAUDE.md`, or `.termux-agent/rules.md` in the working directory (and parents up to `$HOME`) are auto-loaded into the agent's instructions — like opencode.
- **Git integration**: the agent can check status, diff, and commit (commit requires confirmation).
- **Session resume**: continue a previous conversation with `--resume` or `/resume`.
- **Context compacting**: `/compact` summarizes old history to save tokens.
- **Automation mode**: `--yes` skips all confirmations (good for scripts).
- **Android storage access**: enable `allow_storage: true` in config to access files in `/storage/emulated/0` (run `termux-setup-storage` first).
- **Multi-provider** presets: OpenAI, Anthropic, OpenRouter, Ollama, Groq, DeepSeek, Gemini, and **OpenCode Zen**.
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

# pipe stdin as the prompt (one-shot)
echo "fix the bugs in main.py" | termux-agent

# machine-readable one-shot output (JSON for scripts)
termux-agent --json "summarize this repo" > result.json

# list sessions
termux-agent --sessions
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