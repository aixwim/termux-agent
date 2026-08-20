"""Auto-completion installation for termux-agent (bash/zsh/fish)."""
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


FISH_SCRIPT = '''\
function __termux_agent_flags
    termux-agent --help 2>/dev/null | grep -oE '\\-\\-[a-z0-9-]+' | sort -u
end

complete -c termux-agent -f
complete -c termux-agent -a '(__termux_agent_flags)'
complete -c termux-agent -l agent -a '(termux-agent --list-agents 2>/dev/null | awk \'{print $1}\')' -d 'agent role'
complete -c termux-agent -l provider -a '(termux-agent --list-providers 2>/dev/null | awk \'{print $1}\')' -d 'provider'
complete -c termux-agent -l models -a '(termux-agent --list-providers 2>/dev/null | awk \'{print $1}\')' -d 'provider'
complete -c termux-agent -l output -r -d 'output file'
complete -c termux-agent -l import -r -d 'file to import'
complete -c termux-agent -l prompt-file -r -d 'prompt file'
complete -c termux-agent -l cwd -r -d 'working directory'
complete -c termux-agent -l image -r -d 'image path'
complete -c termux-agent -l api-key -r -d 'api key'
complete -c termux-agent -l resume -r -d 'session id'
complete -c termux-agent -l export -r -d 'session id'
complete -c termux-agent -l attach -r -d 'file to attach'
complete -c termux-agent -l token-file -r -d 'token file'
complete -c termux-agent -l tls-cert -r -d 'TLS certificate'
complete -c termux-agent -l tls-key -r -d 'TLS private key'
complete -c termux-agent -l bundle -r -d 'backup directory'
complete -c termux-agent -l restore -r -d 'bundle directory'
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
    elif shell == "fish":
        rc = os.path.join(home, ".config", "fish", "completions", "termux-agent.fish")
        os.makedirs(os.path.dirname(rc), exist_ok=True)
        with open(rc, "w", encoding="utf-8") as f:
            f.write(FISH_SCRIPT)
        return rc
    else:
        raise ValueError(f"Unsupported shell: {shell} (use bash, zsh, or fish)")
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