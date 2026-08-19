"""Provider for OpenAI Chat Completions-compatible APIs.
Covers: OpenAI, OpenRouter, Ollama, Groq, DeepSeek, Gemini (compat endpoint)."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Iterable

import httpx

from termux_agent.providers.base import (
    Provider,
    ProviderError,
    StreamEvent,
    ToolSpec,
    normalize_messages,
)

DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=30.0)

_IMAGE_PATTERN = re.compile(r"\[image:\s*([^\]]+)\]")


def _read_image_data_uri(path: str) -> str | None:
    """Read an image file into a data: URI for vision-capable models."""
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def _embed_images(content: str) -> list[dict] | str:
    """Turn '[image: path]' markers in a user message into OpenAI content parts."""
    if "[image:" not in content:
        return content
    parts: list[dict] = []
    pos = 0
    for m in _IMAGE_PATTERN.finditer(content):
        before = content[pos : m.start()]
        if before.strip():
            parts.append({"type": "text", "text": before})
        data_uri = _read_image_data_uri(m.group(1).strip())
        if data_uri:
            parts.append({"type": "image_url", "image_url": {"url": data_uri}})
        else:
            parts.append({"type": "text", "text": f"[image not found: {m.group(1).strip()}]"})
        pos = m.end()
    tail = content[pos:]
    if tail.strip():
        parts.append({"type": "text", "text": tail})
    return parts


def _iter_sse(response: httpx.Response) -> Iterable[str]:
    """Iterate data lines from an SSE stream."""
    buffer = ""
    for chunk in response.iter_lines():
        if chunk is None:
            continue
        if chunk.startswith("data:"):
            data = chunk[len("data:"):].strip()
            if data == "[DONE]":
                return
            if data:
                yield data
        elif chunk.startswith(":"):
            continue
        else:
            buffer = chunk
    if buffer.strip():
        if buffer.strip() == "[DONE]":
            return
        yield buffer.strip()


def _to_openai_wire(messages: list[dict]) -> list[dict]:
    """Convert internal (flat) messages back to the OpenAI wire format for sending."""
    out: list[dict] = []
    for m in normalize_messages(messages):
        role = m["role"]
        if role == "assistant" and m.get("tool_calls"):
            tcs = [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", ""),
                    },
                }
                for tc in m["tool_calls"]
            ]
            out.append({"role": "assistant", "content": m.get("content", ""), "tool_calls": tcs})
        elif role == "user" and isinstance(m.get("content"), str) and "[image:" in m["content"]:
            out.append({"role": "user", "content": _embed_images(m["content"])})
        else:
            out.append(m)
    return out


class OpenAICompatProvider(Provider):
    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        fallback_models: list[str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.fallback_models = fallback_models or []
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _body(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_wire(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = self.build_tool_specs(tools)
        return body

    def stream(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ) -> Iterable[StreamEvent]:
        body = self._body(messages, tools, temperature, max_tokens)
        if os.environ.get("TERMUX_AGENT_DEBUG"):
            import sys

            print(
                f"[debug] POST {self.base_url}/chat/completions\n{json.dumps(body, ensure_ascii=False, indent=1)[:3000]}",
                file=sys.stderr,
            )
        url = f"{self.base_url}/chat/completions"
        try:
            with self._client.stream(
                "POST", url, headers=self._headers(), json=self._body(messages, tools, temperature, max_tokens)
            ) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", errors="replace")
                    raise ProviderError(f"{self.name}: HTTP {resp.status_code} - {body[:500]}")
                pending: dict[int, dict[str, Any]] = {}
                for data in _iter_sse(resp):
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not chunk.get("choices"):
                        if chunk.get("usage"):
                            yield StreamEvent(kind="usage", usage=chunk["usage"])
                        continue
                    choice = chunk["choices"][0]
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        yield StreamEvent(kind="text_delta", text=delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index", 0))
                        entry = pending.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            entry["id"] = tc["id"]
                        if tc.get("function", {}).get("name"):
                            entry["name"] += tc["function"]["name"]
                        if tc.get("function", {}).get("arguments"):
                            entry["arguments"] += tc["function"]["arguments"]
                if pending:
                    tool_calls = []
                    for _, entry in sorted(pending.items()):
                        tool_calls.append(
                            {
                                "id": entry["id"] or f"call_{len(tool_calls)}",
                                "name": entry["name"],
                                "arguments": entry["arguments"],
                            }
                        )
                    yield StreamEvent(kind="tool_calls", tool_calls=tool_calls)
                yield StreamEvent(kind="done")
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name}: connection failed - {e}") from e

    def list_models(self) -> list[str]:
        url = f"{self.base_url}/models"
        try:
            r = self._client.get(url, headers=self._headers())
            r.raise_for_status()
            return [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
        except (httpx.HTTPError, ValueError):
            return []