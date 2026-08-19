"""Auto-completion installation for termux-agent (bash/zsh)."""
from __future__ import annotations

import os

from termux_agent import __version__

BASH_SCRIPT = '''\
_termux_agent() {
  local cur prev
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  local opts="--provider --model --agent --resume --sessions --list-providers --list-agents --init --install-completion --yes -y --help -h"
  case "$prev" in
    --agent)
      COMPREPLY=($(compgen -W "$(termux-agent --list-agents 2>/dev/null | awk '{print $1}')" -- "$cur"))
      return 0
      ;;
    --provider)
      COMPREPLY=($(compgen -W "$(termux-agent --list-providers 2>/dev/null | awk '{print $1}')" -- "$cur"))
      return 0
      ;;
  esac
  if [[ "$cur" == -* ]]; then
    COMPREPLY=($(compgen -W "$opts" -- "$cur"))
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
  local -a agents providers opts
  agents=(${(f)"$(termux-agent --list-agents 2>/dev/null | awk '{print $1}')"})
  providers=(${(f)"$(termux-agent --list-providers 2>/dev/null | awk '{print $1}')"})
  opts=(
    '--provider[Choose provider]' '--model[Model to use]'
    '--agent[Sub-agent]' '--resume[Resume session]'
    '--sessions[List sessions]' '--list-providers[List providers]'
    '--list-agents[List sub-agents]' '--init[Setup configuration]'
    '--install-completion[Install auto-completion]'
    '--yes[Skip confirmations]' '-y[Skip confirmations]'
    '--help[Help]' '-h[Help]'
  )
  case "${words[2]}" in
    --agent) _describe 'agent' agents ;;
    --provider) _describe 'provider' providers ;;
    *) _describe 'opsi' opts ;;
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