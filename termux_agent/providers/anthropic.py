"""Native Anthropic provider (Messages API) with SSE streaming."""
from __future__ import annotations

import json
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


class AnthropicProvider(Provider):
    name = "anthropic"

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
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
        }

    @staticmethod
    def _to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        out: list[dict] = []
        for m in normalize_messages(messages):
            role = m["role"]
            if role == "system":
                system_parts.append(m["content"])
            elif role == "user":
                out.append({"role": "user", "content": m["content"]})
            elif role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.get("tool_call_id", ""),
                                "content": m["content"],
                            }
                        ],
                    }
                )
            elif role == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    args = tc.get("arguments", "")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args.strip() else {}
                        except json.JSONDecodeError:
                            args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", "toolu_1"),
                            "name": tc.get("name", ""),
                            "input": args,
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
        # gabungkan tool_result / pesan user berurutan
        merged: list[dict] = []
        for m in out:
            if (
                merged
                and merged[-1]["role"] == "user"
                and m["role"] == "user"
                and isinstance(merged[-1]["content"], list)
                and isinstance(m["content"], list)
            ):
                merged[-1]["content"].extend(m["content"])
            else:
                merged.append(m)
        return "\n".join(system_parts), merged

    @staticmethod
    def _to_tool_specs(tools: list[ToolSpec]) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]

    def _body(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        system, msgs = self._to_anthropic(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "messages": msgs,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = self._to_tool_specs(tools)
        return body

    def stream(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ) -> Iterable[StreamEvent]:
        url = f"{self.base_url}/v1/messages"
        body = self._body(messages, tools, temperature, max_tokens)
        tool_inputs: dict[int, str] = {}
        tool_names: dict[int, str] = {}
        tool_ids: dict[int, str] = {}
        text_acc: list[str] = []
        try:
            with self._client.stream("POST", url, headers=self._headers(), json=body) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", errors="replace")
                    raise ProviderError(f"{self.name}: HTTP {resp.status_code} - {body[:500]}")
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    etype = ev.get("type")
                    if etype == "message_start":
                        usage = (ev.get("message") or {}).get("usage") or {}
                        input_tokens = int(usage.get("input_tokens", 0) or 0)
                        if input_tokens:
                            yield StreamEvent(
                                kind="usage",
                                usage={
                                    "prompt_tokens": input_tokens,
                                    "total_tokens": input_tokens,
                                },
                            )
                    elif etype == "content_block_start":
                        cb = ev.get("content_block", {})
                        if cb.get("type") == "tool_use":
                            idx = ev.get("index", 0)
                            tool_names[idx] = cb.get("name", "")
                            tool_ids[idx] = cb.get("id", "")
                            tool_inputs.setdefault(idx, "")
                    elif etype == "content_block_delta":
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text_acc.append(delta.get("text", ""))
                            yield StreamEvent(kind="text_delta", text=delta.get("text", ""))
                        elif delta.get("type") == "input_json_delta":
                            idx = ev.get("index", 0)
                            tool_inputs[idx] = tool_inputs.get(idx, "") + delta.get("partial_json", "")
                    elif etype == "message_delta":
                        if ev.get("usage"):
                            output_tokens = int(
                                ev["usage"].get("output_tokens", 0) or 0
                            )
                            yield StreamEvent(
                                kind="usage",
                                usage={
                                    "completion_tokens": output_tokens,
                                    "total_tokens": output_tokens,
                                },
                            )
            tool_calls = []
            for idx, raw in tool_inputs.items():
                tool_calls.append(
                    {
                        "id": tool_ids.get(idx) or f"toolu_{idx}",
                        "name": tool_names.get(idx, ""),
                        "arguments": raw,
                    }
                )
            if tool_calls:
                yield StreamEvent(kind="tool_calls", tool_calls=tool_calls)
            elif text_acc:
                pass
            yield StreamEvent(kind="done")
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name}: connection failed - {e}") from e
