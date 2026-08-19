"""Interface provider LLM yang seragam untuk semua backend."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (subset OpenAI format)


@dataclass
class StreamEvent:
    kind: str  # "text_delta" | "tool_calls" | "usage" | "done"
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Normalisasi semua pesan internal ke bentuk netral:
    - system/user/assistant dengan content str
    - assistant dgn tool_calls: [{"id","name","arguments"(str)}]
    - tool: {"role":"tool","tool_call_id","content"}
    """
    out = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": str(m.get("content", "")),
                }
            )
        elif role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.get("content", "")}
            if m.get("tool_calls"):
                tcs = []
                for tc in m["tool_calls"]:
                    args = tc.get("arguments")
                    if isinstance(args, dict):
                        args = json.dumps(args, ensure_ascii=False)
                    tcs.append(
                        {
                            "id": tc.get("id", ""),
                            "name": tc.get("name", ""),
                            "arguments": args or "",
                        }
                    )
                entry["tool_calls"] = tcs
            out.append(entry)
        else:
            out.append({"role": role, "content": str(m.get("content", ""))})
    return out


class Provider(ABC):
    name: str = "base"
    model: str = ""
    api_key: str | None = None
    base_url: str = ""
    supports_streaming: bool = True

    @abstractmethod
    def stream(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ) -> Iterable[StreamEvent]:
        ...

    @staticmethod
    def build_tool_specs(tools: list[ToolSpec]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]


class ProviderError(Exception):
    pass