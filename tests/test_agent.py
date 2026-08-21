"""Test loop agent end-to-end dengan mock HTTP (tanpa API key)."""
import json
from pathlib import Path

import httpx
import pytest

from termux_agent.agent import Agent
from termux_agent.providers.base import Provider, StreamEvent
from termux_agent.providers.openai_compat import OpenAICompatProvider
from termux_agent.tools.base import ToolContext


class FakeProvider(Provider):
    name = "fake"

    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self._responses = responses
        self.model = "fake-model"

    def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
        return self._responses.pop(0)


def test_agent_runs_tool_then_answers(tmp_path: Path):
    provider = FakeProvider(
        [
            [
                StreamEvent(kind="text_delta", text="Saya cek."),
                StreamEvent(
                    kind="tool_calls",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "name": "read_file",
                            "arguments": json.dumps({"path": "sample.txt"}),
                        }
                    ],
                ),
            ],
            [StreamEvent(kind="text_delta", text="Isinya: `"), StreamEvent(kind="text_delta", text="ini isi sample"), StreamEvent(kind="text_delta", text="`.")],
        ]
    )
    (tmp_path / "sample.txt").write_text("ini isi sample\n")
    agent = Agent(provider, ToolContext(working_dir=tmp_path, confirm_commands=False))
    assert agent.run("baca sample.txt") == "Isinya: `ini isi sample`."
    assert len(agent.messages) == 5  # system, user, assistant(tool), tool, assistant(final)
    assert agent.round_count == 2
    assert agent.tool_call_count == 1
    assert agent.first_token_seconds is not None


def test_agent_handles_bad_tool_arguments(tmp_path: Path):
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_calls", tool_calls=[{"id": "c1", "name": "read_file", "arguments": "not-json"}])],
            [StreamEvent(kind="text_delta", text="selesai")],
        ]
    )
    agent = Agent(provider, ToolContext(working_dir=tmp_path, confirm_commands=False))
    assert agent.run("x") == "selesai"


def test_agent_empty_answer_guard(tmp_path: Path):
    provider = FakeProvider([[StreamEvent(kind="done")]])
    agent = Agent(provider, ToolContext(working_dir=tmp_path, confirm_commands=False), retries=0)
    out = agent.run("x")
    assert "empty response" in out


def test_agent_retries_empty_response_then_falls_back(tmp_path: Path):
    attempts = []

    class EmptyThenFallbackProvider(Provider):
        name = "fake"
        model = "empty-model"
        fallback_models = ["working-model"]

        def stream(self, messages, tools=None, temperature=0.7, max_tokens=8192):
            attempts.append(self.model)
            if self.model == "empty-model":
                yield StreamEvent(kind="done")
                return
            yield StreamEvent(kind="text_delta", text="fallback worked")
            yield StreamEvent(kind="done")

    agent = Agent(
        EmptyThenFallbackProvider(),
        ToolContext(working_dir=tmp_path, confirm_commands=False),
        retries=1,
        retry_backoff=0,
    )

    assert agent.run("x") == "fallback worked"
    assert attempts == ["empty-model", "empty-model", "working-model"]
    assert agent.provider.model == "working-model"
    assert agent.model_attempts == attempts
    assert agent.retry_count == 1
    assert agent.fallback_count == 1
    assert agent.elapsed_seconds >= 0
    assert agent.round_count == 3
    assert agent.tool_call_count == 0
    assert agent.first_token_seconds is not None
    assert agent.last_error is None


def test_agent_max_rounds(tmp_path: Path):
    call = StreamEvent(
        kind="tool_calls",
        tool_calls=[{"id": "c", "name": "list_dir", "arguments": "{}"}],
    )
    provider = FakeProvider([[call]] * 3)
    agent = Agent(provider, ToolContext(working_dir=tmp_path, confirm_commands=False), max_tool_rounds=2)
    out = agent.run("x")
    assert "maximum tool rounds" in out


def test_openai_compat_streams_tool_calls():
    """Verifikasi parsing SSE OpenAI untuk tool_calls terpecah antar chunk."""
    sse = (
        'data: {"choices":[{"index":0,"delta":{"content":"hai"}}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read_","arguments":"{\\"pat"}}]}}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"file","arguments":"h\\":\\"a.txt\\"}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    transport = httpx.MockTransport(
        handler=lambda req: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse.encode(),
        )
    )
    client = httpx.Client(transport=transport)
    prov = OpenAICompatProvider("http://mock/v1", "m", api_key=None, client=client)
    events = list(prov.stream([{"role": "user", "content": "x"}]))
    texts = "".join(e.text for e in events if e.kind == "text_delta")
    calls = [e for e in events if e.kind == "tool_calls"][0].tool_calls
    assert texts == "hai"
    assert calls[0]["id"] == "call_1"
    assert calls[0]["name"] == "read_file"
    assert json.loads(calls[0]["arguments"]) == {"path": "a.txt"}


def test_openai_compat_error_reads_body():
    transport = httpx.MockTransport(handler=lambda req: httpx.Response(429, json={"error": "rate"}))
    client = httpx.Client(transport=transport)
    prov = OpenAICompatProvider("http://mock/v1", "m", api_key=None, client=client)
    from termux_agent.providers.base import ProviderError

    with pytest.raises(ProviderError, match="429"):
        list(prov.stream([{"role": "user", "content": "x"}]))
