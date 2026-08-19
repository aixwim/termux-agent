"""Faktory provider: pilih backend sesuai konfigurasi."""
from __future__ import annotations

from termux_agent.config import ConfigError, resolve_api_key
from termux_agent.providers.anthropic import AnthropicProvider
from termux_agent.providers.base import Provider, ProviderError, StreamEvent, ToolSpec
from termux_agent.providers.openai_compat import OpenAICompatProvider


def create_provider(name: str, cfg: dict, model: str | None = None) -> Provider:
    providers = cfg.get("providers", {})
    if name not in providers:
        raise ConfigError(f"Provider '{name}' tidak dikenal. Tersedia: {', '.join(providers)}")
    pc = providers[name]
    ptype = pc.get("type", "openai_compat")
    base_url = pc.get("base_url", "")
    if not base_url:
        raise ConfigError(f"Provider '{name}' belum punya base_url")
    models = pc.get("models") or []
    chosen = model or cfg.get("model") or (models[0] if models else "")
    if not chosen:
        raise ConfigError(f"Provider '{name}' tidak punya model. Atur 'model' atau tambah ke models.")
    api_key = resolve_api_key(pc)
    if ptype == "anthropic":
        return AnthropicProvider(base_url, chosen, api_key=api_key)
    return OpenAICompatProvider(base_url, chosen, api_key=api_key)


__all__ = [
    "AnthropicProvider",
    "OpenAICompatProvider",
    "Provider",
    "ProviderError",
    "StreamEvent",
    "ToolSpec",
    "create_provider",
]