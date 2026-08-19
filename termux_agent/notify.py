"""Optional Termux notifications when a task finishes (long one-shot runs)."""
from __future__ import annotations

import os
import shutil
import subprocess


def notify_on_done(enabled: bool) -> None:
    if enabled:
        os.environ["TERMUX_AGENT_NOTIFY"] = "1"


def notify(message: str) -> bool:
    """Send a termux-notification if enabled and available. Returns True if sent."""
    if not os.environ.get("TERMUX_AGENT_NOTIFY") == "1":
        return False
    if not shutil.which("termux-notification"):
        return False
    try:
        subprocess.run(
            ["termux-notification", "--title", "termux-agent", "--content", message[:200]],
            timeout=10,
            capture_output=True,
        )
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False