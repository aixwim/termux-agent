#!/usr/bin/env bash
# Instalasi termux-agent di Termux (Android).
set -euo pipefail

echo "==> Memperbarui pkg..."
pkg update -y
pkg install -y python git

cd "$HOME"
if [ ! -d termux-agent ]; then
  echo "==> Clone repo..."
  git clone https://github.com/ANDA/termux-agent.git
fi
cd termux-agent

echo "==> Memasang dependensi (menciptakan perintah termux-agent)..."
pip install .

echo ""
echo "Selesai. Langsung pakai:"
echo "  termux-agent                                      # mode interaktif (default: OpenCode Zen free)"
echo "  termux-agent \"baca main.py lalu perbaiki bug\"     # one-shot"
echo ""
echo "Opsional, untuk provider berbayar:"
echo "  export OPENCODE_API_KEY=...   # atau OPENAI_API_KEY, ANTHROPIC_API_KEY, dst."