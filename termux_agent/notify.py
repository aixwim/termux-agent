"""Optional Termux integrations: notifications, wake lock, and text-to-speech.

These all rely on termux-api packages: termux-api (notify), termux-api-tools
or termux-api (wake lock / tts). They are all optional and no-ops when missing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import json


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def notify_on_done(enabled: bool) -> None:
    if enabled:
        os.environ["TERMUX_AGENT_NOTIFY"] = "1"


def notify(message: str) -> bool:
    """Send a termux-notification if enabled and available. Returns True if sent."""
    if not os.environ.get("TERMUX_AGENT_NOTIFY") == "1":
        return False
    if not _have("termux-notification"):
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


def wake_lock() -> bool:
    """Prevent the CPU from sleeping while a long task runs (needs termux-api)."""
    if not _have("termux-wake-lock"):
        return False
    try:
        subprocess.run(["termux-wake-lock"], timeout=10, capture_output=True)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def wake_unlock() -> None:
    if not _have("termux-wake-unlock"):
        return
    try:
        subprocess.run(["termux-wake-unlock"], timeout=10, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


def speak(text: str) -> bool:
    """Read text aloud via termux-tts-speak (needs termux-api). Returns True if spoken."""
    if not _have("termux-tts-speak"):
        return False
    try:
        subprocess.run(["termux-tts-speak", text[:1000]], timeout=30, capture_output=True)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def clipboard_get() -> str | None:
    """Read the clipboard via termux-clipboard-get (needs termux-api)."""
    if not _have("termux-clipboard-get"):
        return None
    try:
        proc = subprocess.run(
            ["termux-clipboard-get"], timeout=10, capture_output=True, text=True
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def screenshot(path: str | None = None) -> str | None:
    """Capture the phone screen with termux-screenshot (needs termux-api + screen share).

    Returns the PNG path, or None if unavailable/failed.
    """
    if not _have("termux-screenshot"):
        return None
    target = path or os.path.join(os.getcwd(), f"screenshot-{int(__import__('time').time())}.png")
    try:
        subprocess.run(["termux-screenshot", "-o", target], timeout=30, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return target if os.path.exists(target) else None


def _json_status(cmd: str, timeout: int = 10) -> dict | None:
    if not _have(cmd):
        return None
    try:
        proc = subprocess.run([cmd], timeout=timeout, capture_output=True, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout or "{}")
    except Exception:  # noqa: BLE001
        return None


def device_context() -> str:
    """Best-effort device context string (battery/wifi/time) for the system prompt."""
    import datetime
    import platform

    parts = [
        f"time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M %Z')}",
        f"platform: {platform.platform()}",
    ]
    battery = _json_status("termux-battery-status")
    if battery and battery.get("percentage") is not None:
        status = battery.get("status", "unknown")
        temp = battery.get("temperature")
        line = f"battery: {int(battery['percentage'])}% ({status})"
        if temp is not None:
            line += f", {temp} C"
        parts.append(line)
    wifi = _json_status("termux-wifi-connectioninfo")
    if wifi and wifi.get("ssid"):
        parts.append(f"wifi: {wifi['ssid']} (rssi {wifi.get('rssi')})")
    return "\n".join(parts)