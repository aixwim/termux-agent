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
- **HTTP API**: `--serve` runs a tiny local server (`POST /chat`, `GET /health`, `GET /models`, `GET /sessions`, with CORS enabled for browser clients). Every request is saved as a session and the id is returned in the response; pass `"session": "<id>"` to resume that conversation, or `"stream": true` for Server-Sent Event output. Use `--token` to require a bearer token on all endpoints except `/health`.
- **Resilience knobs**: `--retries N` overrides the transient retry count and `--no-fallback` disables fallback models on 429/errors.
- **Scriptable & extensible**: `--sessions --json`, `--bench --json`, and `--version --json` emit machine-readable output; `--rules FILE` injects extra instructions for one run; `--system-prompt FILE` replaces the whole prompt for a custom persona; `--resume` now supports `--stream`.
- **Watch mode**: `--watch SECONDS` re-runs a one-shot prompt on an interval until Ctrl+C (combine with `--screenshot` to monitor the screen). In the REPL, `/system` shows the effective system prompt.
- **Device awareness & introspection**: `--context` injects battery/wifi/time into the system prompt (needs termux-api); `--doctor --doctor-termux` verifies which termux-api commands are installed; `--list-tools` lists registered tools; `--list-providers --json`, `--list-agents --json`, and `--models --json` give machine-readable introspection; `--config-show` prints the effective merged config; `--config-set KEY VALUE` sets and saves a value (dot paths navigate nested keys, e.g. `providers.zen.model`); `--prune --json` reports what was deleted. In the REPL, `/memory` shows (or clears) the persistent memory.
- **Batch & housekeeping**: `--batch FILE` runs one one-shot per line (results to `--output` as JSON); `--sessions --search` now matches across all messages; `--prune-days N` deletes sessions older than N days.
- **Safety & resource limits**: `--no-shell`, `--no-web`, `--no-git` disable whole tool groups (useful for untrusted/unattended use); `--only-tools LIST` restricts to exact tool names; `--allow-dir DIR` grants file access to extra directories (repeatable); `--max-output-chars N` and `--command-timeout SECONDS` override config limits. In the REPL, `/context` attaches or refreshes device context and `/image PATH` attaches an image.
- **Review & scripting**: `--show [SESSION]` prints a full transcript (plus a rough token estimate); `--summarize [SESSION]` distills a conversation via the agent (`--notify` supported); `--rerun [SESSION]` re-runs the session's last question with the current model (`--diff` compares against the old answer, `--notify` sends a Termux notification); `--export --markdown` writes a readable transcript; `--export-all --markdown` dumps every session as `.md`; `--tokens FILE` estimates token usage; `--tokens --json` returns a structured count; `--no-save` keeps one-shot runs out of the session store; `--no-memory` ignores saved notes; `--session-dir DIR` uses an alternate session store; `--git` injects repo state into the system prompt; `--log FILE` writes a timestamped JSONL run log (one-shot, batch, watch, and REPL turns); `--batch --workers N` runs prompts in parallel (read from stdin with `--batch -`) and `--fail-fast` aborts at the first error; `--forget --json` reports deletions; `--bundle`/`--restore` back up and restore config+memory+sessions (`-` streams/pulls a gzipped tar via stdin/stdout, so `termux-agent --bundle - > b.tgz` and `cat b.tgz | termux-agent --restore -`); `--cron` prints a ready-to-add cron line (`--cron --json` returns it structured); `--no-color` disables ANSI; `--prune`/`--prune-days` accept `--dry-run` to preview deletions (`--prune-days --keep N` keeps the N newest); `--watch` can be capped with `--max-rounds` or `--max-wait` (seconds), `--output FILE` writes the latest answer each round, and `--exit-on-change` stops as soon as the answer changes; `--init --force` overwrites an existing config; `--screenshot-dir DIR` stores captures elsewhere and `--cleanup` removes leftover screenshots. The HTTP API can run detached with `--serve-background`/`--serve-stop` (pid file under `~/.termux-agent/`), read its bearer token from `--token-file`, auto-assign a port with `--port 0`, restrict the CORS origin with `--cors-origin`, serve HTTPS with `--tls-cert`/`--tls-key`, log every request as JSONL with `--log FILE`, and exposes `GET /tools`, `GET /agents`, `GET /stats`, `GET /memory`, `GET /sessions/<id>` (`?markdown=1` for a readable transcript) and `DELETE /sessions/<id>`; `POST /chat` accepts per-request `provider`/`model`/`rules`/`image` (path or URL)/`only_tools`/`temperature`/`max_tool_rounds`/`max_context_tokens`/`system_prompt`/`notify` overrides, `POST /memory` updates the persistent notes, and `POST /batch` runs a list of prompts in parallel. `GET /health` reports version, pid, and uptime; `GET /models?provider=X` lists a provider's models. `--provider zen:model` is a shorthand for setting both at once, `--no-stream` forces a non-streaming answer, `--attach FILE` reads file contents into the prompt, `--completion SHELL` prints a completion script, `--rotate` falls back to the next provider model on failure, `--show-system-prompt` prints the effective system prompt, and `--watch --notify` sends a Termux notification each round (`--watch --diff` only shows rounds whose answer changed).
- **Chat mode**: `--chat` disables all tools for a plain conversation (no file/command access).
- **Timeouts & saving**: `--timeout SECONDS` aborts a slow one-shot task (exit 124); one-shot tasks are now saved as sessions too, so you can `--resume` them.
- **Session backup**: `--export [ID]` prints a session as portable JSON (redirect to a file, `--redact` masks secrets), `--export-all DIR` backs up every session, `--import FILE` restores one (`-` reads stdin), `--prune N` / `--forget [ID]` delete sessions. `--bench [PROVIDER]` times one tiny request per model to help you pick a fast default.
- **Phone-native input**: `--clip` reads the prompt from the clipboard, `--screenshot` captures the screen with `termux-screenshot` and attaches it as an image (both need termux-api). Auto-completion (`--install-completion`) derives flags from `--help`, so it never goes stale.
- **Streaming**: `--stream` prints the answer as it is generated; `--prompt-file -` reads the prompt from stdin. In the REPL, `/plan` toggles plan-first mode (read-only plan, then approve before execution).
- **Config flexibility**: `--config FILE` uses a specific config file (project-level `.termux-agent/config.yaml` files still merge on top, and the home config is never double-loaded); `--init --provider X --model Y` sets up a config without the wizard, and `--config-set temperature 0.2` changes a value later (dot paths work too, e.g. `providers.zen.model`).
- **Scriptable resumes**: `--resume` supports `--json` and `--quiet` for automated continuation; `--sessions --search "keyword"` filters sessions.
- **Scripting output**: `--json` (structured result) and `--quiet` (only the answer, no banner) for pipelines; `--copy` sends the answer to the clipboard; `--stats` prints token usage after the answer.
- **Pipe-friendly**: `echo "fix the bug" | termux-agent` runs a one-shot using stdin as the prompt.
- **Rate-limit fallback**: `fallback_models` in the provider config are tried automatically when the main model returns HTTP 429 (rate limited).
- **Flaky-network retries**: transient failures (network blips, HTTP 5xx) are retried automatically (`retries`, `retry_backoff` in config) — handy on mobile connections.
- **Persistent memory**: `/remember <text>` appends notes to `~/.termux-agent/memory.md`, which is loaded into every new session's instructions.
- **Undo file changes**: `/undo` in interactive mode restores the most recent file write/edit (the agent keeps a snapshot of every changed file).
- **JSON output**: `termux-agent --json "prompt"` prints a machine-readable result `{ok, answer, tool_calls, usage, provider, model}`.
- **Web search without an API key**: `web_search` uses DuckDuckGo, with a Wikipedia fallback when the network blocks/rejects certificates.
- **Image / vision input**: `--image photo.jpg` or the inline marker `[image: path]` in a prompt attach a picture (e.g. a screenshot) for vision-capable models; `--image https://...` downloads a remote image first.
- **Auto-completion**: `--install-completion bash|zsh` adds Tab-completion to your shell (providers, agents, and CLI options). `--help-json` prints the full CLI reference as machine-readable JSON for tools and docs.
- **Diagnostics**: `--doctor` checks the Termux environment, config, PATH, free disk space, session storage, and API key; `--doctor-network` also tests provider connectivity; `--smoke` sends a tiny real prompt end-to-end to verify the whole pipeline (`--smoke --json` returns a structured result).
- **Project rules**: `AGENTS.md`, `CLAUDE.md`, or `.termux-agent/rules.md` in the working directory (and parents up to `$HOME`) are auto-loaded into the agent's instructions — like opencode.
- **Project-local config**: a `.termux-agent/config.yaml` in the current directory (or any parent up to `$HOME`) overrides the global `~/.termux-agent/config.yaml` for that project. `--config-show --redact` prints the merged config with secrets masked.
- **Session instructions**: `/prompt <text>` adds a persistent instruction for the rest of the session (`/prompt` shows them, `/prompt clear` removes them).
- **Git integration**: the agent can check status, diff, recent history, and commit (commit requires confirmation).
- **Session resume**: continue a previous conversation with `--resume` or `/resume`.
- **Session search**: `/search TERM` in the REPL finds sessions whose transcript contains the term. `/attach FILE` (or a URL) reads content into the next turn, `/retry` re-runs the last turn, `/quiet` toggles streaming (answer prints when done), `/temp N` sets sampling temperature, `/maxrounds N` the tool-round limit, and `/usage` shows token usage for the session.
- **Session backup**: `--export [ID]` prints a session as portable JSON (redirect to a file), `--export-all DIR` backs up every session (or `--export-all FILE --json` writes one combined JSON), `--import FILE` restores one (`-` reads stdin, `--json` reports the new id), `--prune N` / `--forget [ID]` delete sessions. `--bundle -` streams a gzipped tar of config+memory+sessions to stdout.
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
# ...or create a config non-interactively
termux-agent --init --provider openai --model gpt-4o-mini

# install Tab-completion (bash/zsh) once
termux-agent --install-completion bash
source ~/.bashrc

# diagnose environment & config (add --json for machine-readable output)
termux-agent --doctor
termux-agent --doctor --json > health.json

# use a different config file (project files still merge on top)
termux-agent --config ./my-config.yaml "check this repo"
termux-agent --doctor-network      # also check provider connectivity

# machine-readable outputs for scripts
termux-agent --sessions --json > sessions.json
termux-agent --bench zen --json > benchmark.json
termux-agent --version --json

# extra per-invocation instructions (like AGENTS.md, just for this run)
echo "Never touch the deploy script." > /tmp/rules.txt
termux-agent --rules /tmp/rules.txt "review the repo"

# full custom persona (replaces the built-in system prompt)
cat > /tmp/pirate.md <<'EOF'
You are a pirate code reviewer. Always use nautical metaphors and be terse.
EOF
termux-agent --system-prompt /tmp/pirate.md "review my code"

# watch mode: re-run a task every 30s, re-attaching a screenshot each round
termux-agent --watch 30 --screenshot --context "what changed on my screen?"

# diagnose the environment, including termux-api availability
termux-agent --doctor --doctor-termux

# give the agent device context (battery/wifi/time, needs termux-api)
termux-agent --context "if battery is low, keep the answer very short"

# restrict what the agent may touch (great for unattended scripts)
termux-agent --no-shell --no-git "summarize the changes in this repo"
termux-agent --no-web --command-timeout 5 --max-output-chars 10000 "explain main.py"
termux-agent --only-tools read_file,grep,glob "where is the login handler?"

# give the agent the repo state as context (status/diff/log)
termux-agent --git "suggest the next commit for this repo"

# inspect what the agent can do and what config it will use
termux-agent --list-tools
termux-agent --config-show
termux-agent --config-show --json > effective-config.json

# bulk: run one one-shot per line of a file, save results as JSON
printf "summarize main.py\ncheck for bugs in utils.py\n" > tasks.txt
termux-agent --batch tasks.txt --output results.json
termux-agent --batch tasks.txt --workers 4 --no-save   # parallel

# run without the persistent memory file
termux-agent --no-memory "one-off question, ignore my saved notes"

# keep a structured audit trail of an unattended run
termux-agent --log run.jsonl --no-save --no-shell "scan this repo for secrets"

# dump every session as readable transcripts
termux-agent --export-all ./archive --markdown

# review or audit a conversation; export it as a readable transcript
termux-agent --show            # latest session
termux-agent --show 20260820-000001
termux-agent --export --markdown > session.md

# get a distilled summary of a long session
termux-agent --summarize --output summary.md
termux-agent --summarize 20260820-000001 --json

# re-run a session's last question with the current model
termux-agent --rerun --json
termux-agent --rerun 20260820-000001 --model nemotron-3-ultra-free
termux-agent --rerun 20260820-000001 --attach notes.md

# machine-readable introspection
termux-agent --list-agents --json
termux-agent --models --json

# keep separate stores for different projects
termux-agent --session-dir ./sessions --sessions

# portable backup / restore of config + memory + sessions
termux-agent --bundle ./backup-$(date +%F)
termux-agent --restore ./backup-2026-08-20

# safe pruning — preview before deleting
termux-agent --prune 5 --dry-run
termux-agent --prune-days 30 --dry-run --json

# batch: stop at the first failure
termux-agent --batch prompts.txt --fail-fast

# watch a limited number of rounds
termux-agent --watch 60 --max-rounds 5 "check my inbox"

# watch, printing one JSON line per round (script-friendly)
termux-agent --watch 60 --json "check my inbox"

# import a session from stdin
cat backup.json | termux-agent --import -

# provider/model shorthand
termux-agent --provider zen:nemotron-3-ultra-free "hello"

# watch with a Termux notification each round
termux-agent --watch 60 --notify "check my inbox"

# attach a file's contents into the prompt
termux-agent --attach notes.md "summarize these notes"
termux-agent --attach a.py --attach b.py "explain the differences"

# batch prompts from stdin
printf 'summarize notes.md\ncheck disk usage\n' | termux-agent --batch -

# re-run and diff the new answer against the old one
termux-agent --rerun 20260820-000001 --diff

# cap the session listing
termux-agent --sessions --limit 5

# force a non-streaming answer
termux-agent --no-stream "summarize this"

# rotate to the next model if the primary fails (rate limits)
termux-agent --rotate "summarize this"

# inspect the effective system prompt without running a turn
termux-agent --show-system-prompt

# write a transcript to a file
termux-agent --show 20260820-000001 --output session.md

# validate a session file without importing it
termux-agent --import backup.json --dry-run

# only show watch rounds whose answer changed
termux-agent --watch 60 --diff "check my inbox"

# print (not install) a shell completion script
termux-agent --completion bash > /tmp/termux-agent-completion.bash

# server stats for scripting
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/stats

# batch with a completion notification
termux-agent --batch prompts.txt --notify

# server: read/write memory, run batches via the API
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/memory
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"remember to water the plants"}' http://127.0.0.1:8787/memory
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompts":["summarize log.txt","check disk usage"]}' http://127.0.0.1:8787/batch

# server: constrain tools per request, query a provider's models
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt":"read config.yaml","only_tools":["read_file"]}' http://127.0.0.1:8787/chat
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8787/models?provider=zen"

# schedule a recurring task with cron (cronie on Termux)
termux-agent --cron '*/10 * * * *' "backup my notes"

# run the HTTP API in the background, then stop it later
termux-agent --serve --token-file ~/.termux-agent/api.token --serve-background
termux-agent --serve-stop
termux-agent --serve --port 0   # auto-assign a port (prints the real one)

# plain output for scripts
termux-agent --no-color --quiet "summarize main.py"

# grant access to extra directories beyond the working dir
termux-agent --allow-dir ~/storage/downloads --allow-dir ./shared "organize the downloads"

# screenshots to a folder, and tidy up leftovers
termux-agent --screenshot --screenshot-dir ~/shots "what's on screen?"
termux-agent --cleanup

# estimate token usage before a big prompt
termux-agent --tokens main.py
wc -c < prompt.txt | xargs printf 'prompt chars: %s\n'

# ephemeral runs leave no trace in the session store
termux-agent --no-save "one-off question"

# housekeeping by age instead of count
termux-agent --prune-days 30

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

# tune resilience: more transient retries, or never fall back to another model
termux-agent --retries 3 --timeout 300 "deploy everything"
termux-agent --no-fallback "use exactly my configured model"

# run a local HTTP API (for Tasker / Termux:API / scripts)
termux-agent --serve --host 127.0.0.1 --port 8787
curl -X POST http://127.0.0.1:8787/chat -d '{"prompt":"hello"}' -H 'Content-Type: application/json'
# every request is saved; pass the returned "session" id to resume it:
curl -X POST http://127.0.0.1:8787/chat -d '{"prompt":"continue","session":"20260819-..."}' -H 'Content-Type: application/json'

# protect the API with a bearer token (needed for any non-/health request)
termux-agent --serve --token "a-long-random-string"
curl -X POST http://127.0.0.1:8787/chat -d '{"prompt":"hello"}' -H 'Authorization: Bearer a-long-random-string'
curl http://127.0.0.1:8787/sessions -H 'Authorization: Bearer a-long-random-string'   # list saved sessions

# stream responses as Server-Sent Events (for Tasker / web clients)
curl -N -X POST http://127.0.0.1:8787/chat -d '{"prompt":"explain","stream":true}'

# list sessions
termux-agent --sessions

# backup / move a session to another device (portable JSON on stdout)
termux-agent --export > backup.json

termux-agent --export --redact > backup-sanitized.json
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

# use a different config file (project files still merge on top)
termux-agent --config ./my-config.yaml "check this repo"

# create a config non-interactively
termux-agent --init --provider openai --model gpt-4o-mini

# machine-readable diagnostics
termux-agent --doctor --json > health.json
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
/system        show the effective system prompt
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