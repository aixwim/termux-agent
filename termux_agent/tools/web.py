"""Tool web: ambil isi URL dan cari di web (DuckDuckGo, tanpa API key)."""
from __future__ import annotations

import html
import json
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


def _http_json(url: str, max_bytes: int = 200_000) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "termux-agent/0.2"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read(max_bytes).decode("utf-8", errors="replace"))


def _search_ddg(query: str, max_results: int) -> list[str]:
    params = urllib.parse.urlencode(
        {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
    )
    data = _http_json(f"https://api.duckduckgo.com/?{params}")
    results: list[str] = []
    if data.get("AbstractText"):
        results.append(f"Ringkasan: {data['AbstractText'][:400]}\n  {data.get('AbstractURL', '')}")
    for topic in data.get("RelatedTopics", []):
        if "Topics" in topic:
            for t in topic["Topics"]:
                results.append(f"- {t.get('Text', '')}\n  {t.get('FirstURL', '')}")
        elif topic.get("Text"):
            results.append(f"- {topic['Text']}\n  {topic.get('FirstURL', '')}")
        if len(results) >= max_results:
            break
    return results[:max_results]


def _search_wikipedia(query: str, max_results: int) -> list[str]:
    params = urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": max_results}
    )
    data = _http_json(f"https://en.wikipedia.org/w/api.php?{params}")
    results: list[str] = []
    for s in data.get("query", {}).get("search", []):
        title = s.get("title", "")
        snippet = html.unescape(re.sub(r"<[^>]+>", "", s.get("snippet", "")))
        url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        results.append(f"- {title}: {snippet}\n  {url}")
    return results[:max_results]


@tool(
    "web_search",
    "Cari informasi di web. Untuk baca halaman hasil, gunakan web_fetch.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Kata kunci pencarian"},
            "max_results": {"type": "integer", "description": "Jumlah hasil (default 5)"},
        },
        "required": ["query"],
    },
)
def web_search(args: dict, ctx: ToolContext) -> str:
    query = str(args["query"])
    max_results = int(args.get("max_results", 5))
    backends = [("DuckDuckGo", _search_ddg), ("Wikipedia", _search_wikipedia)]
    last_err = ""
    for label, fn in backends:
        try:
            results = fn(query, max_results)
            if results:
                return f"Hasil pencarian '{query}' ({label}):\n" + "\n\n".join(results)
            last_err = f"{label}: tidak ada hasil"
        except Exception as e:  # noqa: BLE001
            last_err = f"{label}: {type(e).__name__}: {e}"
    return f"Tidak ada hasil untuk: {query} ({last_err})"