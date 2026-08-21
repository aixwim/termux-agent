from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from termux_agent.images import (
    ImageDownloadError,
    cleanup_downloaded_image,
    decode_data_image,
    download_image,
    read_image,
)


class _Response(BytesIO):
    def __init__(self, data: bytes, length: str | None = None):
        super().__init__(data)
        self.headers = {} if length is None else {"Content-Length": length}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_download_image_uses_detected_format_and_unique_files(monkeypatch):
    data = b"\x89PNG\r\n\x1a\ncontent"
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Response(data))

    first, size = download_image("https://example.test/not-an-image.txt")
    second, _ = download_image("https://example.test/not-an-image.txt")
    try:
        assert size == len(data)
        assert first.suffix == ".png"
        assert first != second
        assert first.read_bytes() == data
    finally:
        cleanup_downloaded_image(first)
        cleanup_downloaded_image(second)


def test_download_image_rejects_oversized_content_length(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Response(b"", "101"))

    with pytest.raises(ImageDownloadError, match="exceeds"):
        download_image("https://example.test/image.png", max_bytes=100)


def test_download_image_rejects_oversized_stream(monkeypatch):
    data = b"\x89PNG\r\n\x1a\n" + (b"x" * 100)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Response(data))

    with pytest.raises(ImageDownloadError, match="exceeds"):
        download_image("https://example.test/image.png", max_bytes=16)


def test_download_image_rejects_non_image(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Response(b"<html>no</html>"))

    with pytest.raises(ImageDownloadError, match="not a supported"):
        download_image("https://example.test/image.png")


def test_cli_command_cleanup_keeps_image_available_during_command(tmp_path: Path):
    from termux_agent.cli import _run_with_image_cleanup

    image = tmp_path / "download.png"
    image.write_bytes(b"image")

    def command():
        assert image.is_file()
        return 7

    assert _run_with_image_cleanup(image, command) == 7
    assert not image.exists()


def test_read_image_detects_content_instead_of_extension(tmp_path: Path):
    image = tmp_path / "misleading.txt"
    image.write_bytes(b"\xff\xd8\xffcontent")

    data, mime = read_image(image)

    assert data.startswith(b"\xff\xd8\xff")
    assert mime == "image/jpeg"


def test_read_image_rejects_large_local_file(tmp_path: Path):
    image = tmp_path / "large.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 20)

    with pytest.raises(ImageDownloadError, match="exceeds"):
        read_image(image, max_bytes=16)


def test_cli_rejects_invalid_local_image_before_command(tmp_path: Path, monkeypatch):
    import io

    from termux_agent import cli

    image = tmp_path / "fake.png"
    image.write_bytes(b"not an image")
    called = False

    def command(*args, **kwargs):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "cmd_one_shot", command)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    assert cli.main(["--image", str(image), "describe"]) == 1
    assert called is False


def test_decode_data_image_validates_and_stores_png():
    import base64

    data = b"\x89PNG\r\n\x1a\ncontent"
    uri = "data:image/png;base64," + base64.b64encode(data).decode()
    image = decode_data_image(uri)
    try:
        assert image.suffix == ".png"
        assert image.read_bytes() == data
    finally:
        cleanup_downloaded_image(image)


def test_decode_data_image_rejects_invalid_base64():
    with pytest.raises(ImageDownloadError, match="invalid base64"):
        decode_data_image("data:image/png;base64,not!base64")


def test_decode_data_image_rejects_non_image_payload():
    import base64

    uri = "data:image/png;base64," + base64.b64encode(b"not an image").decode()
    with pytest.raises(ImageDownloadError, match="not a supported"):
        decode_data_image(uri)


def test_openai_server_prompt_rejects_invalid_image_data_uri():
    from termux_agent.server import _AgentHandler

    handler = object.__new__(_AgentHandler)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,invalid!"},
                },
            ],
        }
    ]

    with pytest.raises(ImageDownloadError, match="invalid base64"):
        handler._openai_prompt(messages)
