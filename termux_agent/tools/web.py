"""Tool web: ambil isi URL dan ubah ke teks sederhana."""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request

from termux_agent.tools.base import ToolContext, tool


@tool(
    "web_fetch",
    "Ambil isi halaman web/API dari URL. Hasil diubah ke teks polos (tag HTML dihilangkan).",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL lengkap (http/https)"},
            "max_chars": {"type": "integer", "description": "Batas karakter hasil (default 20000)"},
        },
        "required": ["url"],
    },
)
def web_fetch(args: dict, ctx: ToolContext) -> str:
    url = str(args["url"])
    max_chars = int(args.get("max_chars", 20000))
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Error: URL harus http/https: {url}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "termux-agent/0.1 (+localhost)"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read(200_000).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return f"Error: gagal fetch {url}: {e}"
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "... [terpotong]"
    return f"URL: {url}\n{text}"