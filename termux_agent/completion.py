"""Auto-completion installation for termux-agent (bash/zsh)."""
from __future__ import annotations

import os

from termux_agent import __version__

BASH_SCRIPT = '''\
_termux_agent_flags() {
  termux-agent --help 2>/dev/null | grep -oE '\\-\\-[a-z0-9-]+' | sort -u
}

_termux_agent() {
  local cur prev
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  case "$prev" in
    --agent)
      COMPREPLY=($(compgen -W "$(termux-agent --list-agents 2>/dev/null | awk '{print $1}')" -- "$cur"))
      return 0
      ;;
    --provider|--models)
      COMPREPLY=($(compgen -W "$(termux-agent --list-providers 2>/dev/null | awk '{print $1}')" -- "$cur"))
      return 0
      ;;
    --import|--output|--prompt-file|--cwd|--image|--api-key)
      COMPREPLY=($(compgen -f -- "$cur"))
      return 0
      ;;
    --resume|--export|--search|--timeout|--port|--max-context-tokens|--max-tool-rounds|--temperature|--prune)
      COMPREPLY=($(compgen -W "" -- "$cur"))
      return 0
      ;;
  esac
  if [[ "$cur" == -* ]]; then
    COMPREPLY=($(compgen -W "$(_termux_agent_flags)" -- "$cur"))
  else
    COMPREPLY=($(compgen -f -- "$cur"))
  fi
  return 0
}
complete -o default -F _termux_agent termux-agent
'''

ZSH_SCRIPT = '''\
#compdef termux-agent
_termux_agent() {
  local -a agents providers flags
  agents=(${(f)"$(termux-agent --list-agents 2>/dev/null | awk '{print $1}')"})
  providers=(${(f)"$(termux-agent --list-providers 2>/dev/null | awk '{print $1}')"})
  flags=(${(f)"$(termux-agent --help 2>/dev/null | grep -oE '\\-\\-[a-z0-9-]+' | sort -u)"})
  case "${words[2]}" in
    --agent) _describe 'agent' agents ;;
    --provider|--models) _describe 'provider' providers ;;
    --import|--output|--prompt-file|--cwd|--image|--api-key) _files ;;
    *) _arguments '*:option:(${flags})' ;;
  esac
}
compdef _termux_agent termux-agent
'''


def install(shell: str) -> str:
    shell = shell.lower()
    home = os.path.expanduser("~")
    if shell == "zsh":
        rc = os.path.join(home, ".zshrc")
        script = ZSH_SCRIPT
    elif shell == "bash":
        rc = os.path.join(home, ".bashrc")
        script = BASH_SCRIPT
    else:
        raise ValueError(f"Unsupported shell: {shell} (use bash or zsh)")
    block = f"\n# >>> termux-agent completion (v{__version__}) >>>\n{script}# <<< termux-agent completion <<<\n"
    if os.path.exists(rc):
        with open(rc, encoding="utf-8") as f:
            content = f.read()
        if "termux-agent completion" in content:
            import re

            content = re.sub(
                r"\n# >>> termux-agent completion.*?# <<< termux-agent completion <<<\n?",
                "",
                content,
                flags=re.S,
            )
        content = content.rstrip("\n") + "\n" + block
    else:
        content = block
    with open(rc, "w", encoding="utf-8") as f:
        f.write(content)
    return rc