"""Konfigurasi termux-agent: load YAML, env override, preset provider."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.environ.get("TERMUX_AGENT_HOME", "~/.termux-agent")).expanduser()
CONFIG_FILE = CONFIG_DIR / "config.yaml"

DEFAULTS: dict[str, Any] = {
    # Default ke OpenCode Zen model free: langsung jalan tanpa API key,
    # seperti opencode yang langsung bisa dipakai setelah install.
    "provider": "zen",
    "model": "nemotron-3-ultra-free",
    "agent": "root",
    "temperature": 0.7,
    "max_tool_rounds": 20,
    "working_dir": "~",
    "confirm_commands": True,
    "command_timeout": 60,
    "max_output_chars": 60000,
    # Izinkan akses ke penyimpanan Android (/storage/emulated/0) oleh tool file.
    # Jalankan dulu: termux-setup-storage (membuat ~/storage).
    "allow_storage": False,
    "providers": {
        "openai": {
            "type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
            "api_key_env": "OPENAI_API_KEY",
        },
        "anthropic": {
            "type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "models": ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"],
            "api_key_env": "ANTHROPIC_API_KEY",
        },
        "openrouter": {
            "type": "openai_compat",
            "base_url": "https://openrouter.ai/api/v1",
            "models": ["anthropic/claude-3.5-sonnet", "openai/gpt-4o", "meta-llama/llama-3.3-70b-instruct"],
            "api_key_env": "OPENROUTER_API_KEY",
        },
        "ollama": {
            "type": "openai_compat",
            "base_url": "http://localhost:11434/v1",
            "models": ["llama3.2", "qwen2.5-coder:7b", "deepseek-coder-v2"],
            "api_key_env": "",
        },
        "groq": {
            "type": "openai_compat",
            "base_url": "https://api.groq.com/openai/v1",
            "models": ["llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b"],
            "api_key_env": "GROQ_API_KEY",
        },
        "deepseek": {
            "type": "openai_compat",
            "base_url": "https://api.deepseek.com/v1",
            "models": ["deepseek-chat", "deepseek-reasoner"],
            "api_key_env": "DEEPSEEK_API_KEY",
        },
        "gemini": {
            "type": "openai_compat",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "models": ["gemini-2.0-flash", "gemini-2.5-flash"],
            "api_key_env": "GEMINI_API_KEY",
        },
        # OpenCode Zen: gratis (model free tanpa API key), atau berbayar dengan OPENCODE_API_KEY
        # dari https://opencode.ai/auth. Endpoint OpenAI-compatible.
        "zen": {
            "type": "openai_compat",
            "base_url": "https://opencode.ai/zen/v1",
            "models": [
                "nemotron-3-ultra-free",
                "deepseek-v4-flash-free",
                "mimo-v2.5-free",
                "big-pickle",
            ],
            "api_key_env": "OPENCODE_API_KEY",
        },
    },
    # Sub-agent: nama -> prompt tambahan + daftar tool yang diizinkan (opsional).
    # Kunci "tools" memuat daftar nama tool yang boleh dipakai (kosong = semua).
    "agents": {
        "root": {
            "description": "Asisten utama dengan semua tool",
            "prompt": "Kamu adalah agent utama dengan akses penuh.",
            "tools": [],
        },
        "explore": {
            "description": "Baca & cari kode tanpa mengubah apa pun",
            "prompt": (
                "Kamu adalah agent eksplorasi. Tugasmu hanya MEMBACA dan MENCARI: "
                "baca file, daftar direktori, grep, glob, status git. "
                "JANGAN menulis, mengedit, atau menjalankan perintah yang mengubah sistem."
            ),
            "tools": ["read_file", "list_dir", "grep_file", "glob_find", "git_status", "git_diff"],
        },
        "coder": {
            "description": "Tulis/edit kode, tanpa commit git",
            "prompt": (
                "Kamu adalah agent koding. Fokus menulis dan mengedit kode dengan benar "
                "mengikuti aturan proyek. Jangan commit ke git."
            ),
            "tools": ["read_file", "write_file", "edit_file", "list_dir", "grep_file", "glob_find", "run_command", "git_status", "git_diff"],
        },
        "shell": {
            "description": "Hanya menjalankan perintah shell",
            "prompt": (
                "Kamu adalah agent shell. Fokus menjalankan perintah Termux dan melaporkan output. "
                "Gunakan run_command untuk semua tugas."
            ),
            "tools": ["run_command", "read_file", "list_dir"],
        },
    },
}


class ConfigError(Exception):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            user = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Gagal parse {CONFIG_FILE}: {e}")
        cfg = _deep_merge(cfg, user)
    return cfg


def resolve_api_key(provider_cfg: dict[str, Any]) -> str | None:
    env_name = provider_cfg.get("api_key_env", "")
    if not env_name:
        return None
    return os.environ.get(env_name) or None


def resolve_working_dir(cfg: dict[str, Any]) -> Path:
    wd = os.path.expanduser(str(cfg.get("working_dir", "~")))
    p = Path(wd).resolve()
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
    return p


def detect_storage_roots() -> list[Path]:
    """Deteksi root penyimpanan Android yang bisa diakses Termux."""
    roots: list[Path] = []
    candidates = [
        Path("/storage/emulated/0"),
        Path.home() / "storage" / "shared",  # hasil termux-setup-storage
        Path.home() / "storage" / "downloads",
    ]
    for c in candidates:
        if c.is_dir() and c.exists():
            roots.append(c)
    return roots


def ensure_config_file() -> Path:
    """Salin config.example.yaml ke ~/.termux-agent/config.yaml bila belum ada."""
    if CONFIG_FILE.exists():
        return CONFIG_FILE
    example = Path(__file__).resolve().parent.parent / "config.example.yaml"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if example.exists():
        CONFIG_FILE.write_text(example.read_text())
    else:
        CONFIG_FILE.write_text(yaml.safe_dump(DEFAULTS, sort_keys=False, allow_unicode=True))
    return CONFIG_FILE