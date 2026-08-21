"""Test provider: konversi pesan internal -> format wire OpenAI/Anthropic."""
import json

import httpx
import pytest

from termux_agent.providers.anthropic import AnthropicProvider
from termux_agent.providers.base import ToolSpec
from termux_agent.providers.openai_compat import _to_openai_wire

TOOLS = [
    ToolSpec(
        name="read_file",
        description="baca file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    )
]


def test_normalize_roundtrip_tool_calls_to_wire():
    msgs = [
        {"role": "system", "content": "sistem"},
        {"role": "user", "content": "baca file"},
        {
            "role": "assistant",
            "content": "siap",
            "tool_calls": [{"id": "call_1", "name": "read_file", "arguments": '{"path":"a.txt"}'}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "hasil"},
    ]
    wire = _to_openai_wire(msgs)
    asst = wire[2]
    assert asst["role"] == "assistant"
    assert asst["content"] == "siap"
    tc = asst["tool_calls"][0]
    assert tc["type"] == "function"
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "read_file"
    assert tc["function"]["arguments"] == '{"path":"a.txt"}'
    assert wire[3]["role"] == "tool"
    assert wire[3]["tool_call_id"] == "call_1"


def test_anthropic_system_and_tool_use():
    system, msgs = AnthropicProvider._to_anthropic(
        [
            {"role": "system", "content": "sistem"},
            {"role": "user", "content": "hai"},
            {
                "role": "assistant",
                "content": "ok",
                "tool_calls": [{"id": "tc1", "name": "ls", "arguments": '{"a":1}'}],
            },
            {"role": "tool", "tool_call_id": "tc1", "content": "hasil"},
        ]
    )
    assert system == "sistem"
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "tc1", "name": "ls", "input": {"a": 1}}
    assert msgs[2]["content"][0]["type"] == "tool_result"


def test_anthropic_merges_consecutive_user_tool_messages():
    _, msgs = AnthropicProvider._to_anthropic(
        [
            {"role": "user", "content": "a"},
            {"role": "tool", "tool_call_id": "t1", "content": "r1"},
            {"role": "tool", "tool_call_id": "t2", "content": "r2"},
        ]
    )
    assert len(msgs) == 2
    assert msgs[1]["content"][0]["tool_use_id"] == "t1"
    assert msgs[1]["content"][1]["tool_use_id"] == "t2"


def test_anthropic_bad_json_arguments_fall_back_to_dict():
    _, msgs = AnthropicProvider._to_anthropic(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t", "name": "x", "arguments": "not-json"}],
            }
        ]
    )
    assert msgs[0]["content"][0]["input"] == {}


def test_tool_specs_build():
    built = AnthropicProvider._to_tool_specs(TOOLS)
    assert built[0]["name"] == "read_file"
    assert built[0]["input_schema"] == TOOLS[0].parameters
    openai_specs = AnthropicProvider.build_tool_specs(TOOLS)
    assert openai_specs[0]["type"] == "function"
    assert openai_specs[0]["function"]["name"] == "read_file"


def test_anthropic_stream_preserves_tool_ids_and_indexes():
    events = [
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_server_1", "name": "read_file"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"path":"a.txt"}'},
        },
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {"type": "tool_use", "id": "toolu_server_2", "name": "list_files"},
        },
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": "{}"},
        },
    ]
    payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    ) as client:
        provider = AnthropicProvider("https://example.test", "model", client=client)
        streamed = list(provider.stream([{"role": "user", "content": "run tools"}]))

    calls = next(event.tool_calls for event in streamed if event.kind == "tool_calls")
    assert calls == [
        {"id": "toolu_server_1", "name": "read_file", "arguments": '{"path":"a.txt"}'},
        {"id": "toolu_server_2", "name": "list_files", "arguments": "{}"},
    ]
