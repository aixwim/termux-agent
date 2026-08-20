#!/usr/bin/env bash
# Install termux-agent on Termux (Android).
set -euo pipefail

echo "==> Updating pkg..."
pkg update -y
pkg install -y python git

cd "$HOME"
if [ ! -d termux-agent ]; then
  echo "==> Cloning repo..."
  git clone https://github.com/aixwim/termux-agent.git
fi
cd termux-agent

echo "==> Installing dependencies (creates the termux-agent command)..."
pip install .

echo ""
echo "Done. Start using it:"
echo "  termux-agent                                    # interactive mode (default: free OpenCode Zen)"
echo "  termux-agent \"read main.py and fix the bug\"     # one-shot"
echo ""
echo "Optional, for paid providers:"
echo "  export OPENCODE_API_KEY=...   # or OPENAI_API_KEY, ANTHROPIC_API_KEY, etc."