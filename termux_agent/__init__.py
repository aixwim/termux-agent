"""Termux Agent - CLI coding agent for Termux, like opencode."""
from importlib import metadata

try:
    __version__ = metadata.version("termux-agent")
except Exception:  # noqa: BLE001 - not installed (running from source tree)
    __version__ = "0.0.0-dev"