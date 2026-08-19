"""Mock server OpenAI-compat + Anthropic untuk uji end-to-end tanpa API key.

OpenAI (POST /v1/chat/completions):
  req 1 -> tool_call read_file
  req 2 (ada hasil tool) -> jawaban akhir
Anthropic (POST /v1/messages):
  req 1 -> content_block tool_use (read_file)
  req 2 (ada tool_result) -> text akhir
"""
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/chat/completions"):
            self._openai(body)
        elif self.path.endswith("/v1/messages"):
            self._anthropic(body)
        else:
            self.send_error(404)

    def _openai(self, body: dict) -> None:
        messages = body.get("messages", [])
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if not has_tool_result:
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": "Saya cek file itu dulu."}}]},
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": '{"path":"sample.txt"}',
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                },
            ]
        else:
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": "## Hasil\n\n"}}]},
                {"choices": [{"index": 0, "delta": {"content": "Isi file: `"}}]},
                {"choices": [{"index": 0, "delta": {"content": "ini isi sample"}}]},
                {"choices": [{"index": 0, "delta": {"content": "`. Selesai."}}]},
            ]
        for c in chunks:
            self.wfile.write(sse(c))
            self.wfile.flush()
            time.sleep(0.05)
        self.wfile.write(sse({"choices": [], "usage": {"total_tokens": 30}}))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _anthropic(self, body: dict) -> None:
        messages = body.get("messages", [])
        has_tool_result = any(
            m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(c.get("type") == "tool_result" for c in m["content"])
            for m in messages
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if not has_tool_result:
            events = [
                {"type": "message_start", "message": {"id": "m1", "role": "assistant", "content": []}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Cek dulu."}},
                {"type": "content_block_stop", "index": 0},
                {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_01", "name": "read_file", "input": {}}},
                {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"pa'}},
                {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": 'th":"sample.txt"}'}},
                {"type": "content_block_stop", "index": 1},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 20}},
                {"type": "message_stop"},
            ]
        else:
            events = [
                {"type": "message_start", "message": {"id": "m2", "role": "assistant", "content": []}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Isinya `ini isi sample`. Selesai dari Claude."}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 20}},
                {"type": "message_stop"},
            ]
        for e in events:
            self.wfile.write(sse(e))
            self.wfile.flush()
            time.sleep(0.05)
        self.wfile.flush()

    def log_message(self, *args):  # noqa: A003
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()