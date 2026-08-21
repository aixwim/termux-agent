from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest

from termux_agent.server import (
    MAX_BATCH_PROMPTS,
    MAX_PROMPT_CHARS,
    MAX_REQUEST_BODY_BYTES,
    RequestBodyError,
    _read_body,
    _authorized,
    _validate_batch,
    _validate_prompt,
)


def _handler(body: bytes, content_length: str | None = None):
    length = str(len(body)) if content_length is None else content_length
    return SimpleNamespace(headers={"Content-Length": length}, rfile=BytesIO(body))


def test_read_body_accepts_json_object():
    assert _read_body(_handler(b'{"prompt":"hello"}')) == {"prompt": "hello"}


@pytest.mark.parametrize("length", ["invalid", "-1"])
def test_read_body_rejects_invalid_content_length(length: str):
    with pytest.raises(RequestBodyError, match="invalid Content-Length"):
        _read_body(_handler(b"{}", length))


def test_read_body_rejects_oversized_request_before_reading():
    handler = _handler(b"", str(MAX_REQUEST_BODY_BYTES + 1))

    with pytest.raises(RequestBodyError, match="16 MiB") as caught:
        _read_body(handler)

    assert caught.value.status == 413


def test_read_body_rejects_incomplete_request():
    with pytest.raises(RequestBodyError, match="incomplete"):
        _read_body(_handler(b"{}", "20"))


@pytest.mark.parametrize("body", [b"not-json", b"\xff"])
def test_read_body_rejects_invalid_json_or_utf8(body: bytes):
    with pytest.raises(RequestBodyError, match="valid UTF-8 JSON"):
        _read_body(_handler(body))


def test_read_body_rejects_non_object_json():
    with pytest.raises(RequestBodyError, match="JSON object"):
        _read_body(_handler(b"[]"))


def test_validate_batch_normalizes_prompts_and_workers():
    prompts, workers = _validate_batch(
        {"prompts": [" first ", "second"], "workers": 1}
    )

    assert prompts == ["first", "second"]
    assert workers == 1


def test_validate_batch_caps_workers_to_prompt_count():
    _, workers = _validate_batch({"prompts": ["only"], "workers": 4})
    assert workers == 1


def test_validate_batch_rejects_too_many_prompts():
    with pytest.raises(RequestBodyError, match="100-prompt"):
        _validate_batch({"prompts": ["x"] * (MAX_BATCH_PROMPTS + 1)})


def test_validate_batch_rejects_oversized_prompt():
    with pytest.raises(RequestBodyError, match="character limit"):
        _validate_batch({"prompts": ["x" * (MAX_PROMPT_CHARS + 1)]})


@pytest.mark.parametrize("workers", [True, 0, 5, "2"])
def test_validate_batch_rejects_invalid_workers(workers):
    with pytest.raises(RequestBodyError, match="workers"):
        _validate_batch({"prompts": ["x"], "workers": workers})


def test_validate_prompt_strips_valid_input():
    assert _validate_prompt("  hello  ") == "hello"


def test_validate_prompt_rejects_oversized_input():
    with pytest.raises(RequestBodyError, match="character limit"):
        _validate_prompt("x" * (MAX_PROMPT_CHARS + 1))


@pytest.mark.parametrize(
    ("path", "payload", "error_key"),
    [
        ("/chat", {"prompt": "hello"}, "error"),
        (
            "/v1/chat/completions",
            {"model": "m", "messages": [{"role": "user", "content": "hello"}]},
            "error",
        ),
    ],
)
def test_non_stream_chat_returns_json_when_agent_fails(
    path: str, payload: dict, error_key: str
):
    import json
    import threading
    import urllib.error
    import urllib.request

    from termux_agent.server import build_server

    def build_agent(*args, **kwargs):
        def fail(prompt, **run_kwargs):
            raise RuntimeError("provider unavailable")

        return SimpleNamespace(
            provider=SimpleNamespace(name="test", model="m"),
            messages=[{"role": "system", "content": "system"}],
            usage={},
            run=fail,
        )

    server = build_server(build_agent, {"provider": "test"}, "test", "m")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        assert caught.value.code == 500
        body = json.loads(caught.value.read())
        error = body[error_key]
        message = error["message"] if isinstance(error, dict) else error
        assert message == "provider unavailable"
    finally:
        server.shutdown()
        server.server_close()


def test_openai_stream_reports_agent_failure_before_done():
    import json
    import threading
    import urllib.request

    from termux_agent.server import build_server

    def build_agent(*args, **kwargs):
        def fail(prompt, **run_kwargs):
            raise RuntimeError("stream provider unavailable")

        return SimpleNamespace(
            provider=SimpleNamespace(name="test", model="m"),
            messages=[],
            usage={},
            run=fail,
        )

    server = build_server(build_agent, {"provider": "test"}, "test", "m")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode()
        assert '"type": "server_error"' in body
        assert '"message": "stream provider unavailable"' in body
        assert body.rstrip().endswith("data: [DONE]")
        assert '"finish_reason": "stop"' not in body
    finally:
        server.shutdown()
        server.server_close()


def test_authorized_supports_unicode_tokens_and_rejects_mismatch():
    handler = SimpleNamespace(
        token="rahasia-🔒",
        headers={"Authorization": "Bearer rahasia-🔒"},
    )
    assert _authorized(handler) is True

    handler.headers["Authorization"] = "Bearer rahasia-x"
    assert _authorized(handler) is False


def test_server_instances_keep_tokens_isolated():
    import json
    import threading
    import urllib.error
    import urllib.request

    from termux_agent.server import build_server

    def build_agent(*args, **kwargs):
        return SimpleNamespace(
            provider=SimpleNamespace(name="test", model="m"),
            messages=[],
            usage={},
            run=lambda prompt, **run_kwargs: "ok",
        )

    first = build_server(build_agent, {"provider": "test"}, "test", "m", token="first")
    second = build_server(build_agent, {"provider": "test"}, "test", "m", token="second")
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (first, second)
    ]
    for thread in threads:
        thread.start()

    def request(server, token):
        payload = json.dumps({"prompt": "hello"}).encode()
        return urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/chat",
                data=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            ),
            timeout=10,
        )

    try:
        with request(first, "first") as response:
            assert json.loads(response.read())["ok"] is True
        with request(second, "second") as response:
            assert json.loads(response.read())["ok"] is True
        with pytest.raises(urllib.error.HTTPError) as caught:
            request(first, "second")
        assert caught.value.code == 401
    finally:
        for server in (first, second):
            server.shutdown()
            server.server_close()
