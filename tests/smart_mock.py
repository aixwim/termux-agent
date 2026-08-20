"""Smart mock server: routes tool calls to real handlers so tool-use loops are tested for real."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer


def handle_tool(name: str, args: dict, workdir: str) -> str:
    if name == "read_file":
        p = os.path.join(workdir, args.get("path", ""))
        return open(p, encoding="utf-8").read() if os.path.exists(p) else f"Error: no such file {p}"
    if name == "write_file":
        p = os.path.join(workdir, args.get("path", ""))
        os.makedirs(os.path.dirname(p) or workdir, exist_ok=True)
        open(p, "w", encoding="utf-8").write(args.get("content", ""))
        return f"Wrote {len(args.get('content', ''))} bytes to {args.get('path')}"
    if name == "list_dir":
        p = os.path.join(workdir, args.get("path", "."))
        return ", ".join(sorted(os.listdir(p)))
    if name == "grep_file":
        pat = args.get("pattern", "")
        p = os.path.join(workdir, args.get("path", "."))
        hits = []
        for root, _dirs, files in os.walk(p):
            for f in files:
                fp = os.path.join(root, f)
                for i, line in enumerate(open(fp, encoding="utf-8", errors="replace"), 1):
                    if re.search(pat, line):
                        hits.append(f"{f}:{i}:{line.strip()}")
        return "\n".join(hits[:20]) or "no matches"
    if name == "run_command":
        return "done\noutput from ls test"
    if name == "glob_find":
        return "found src/math.py"
    return f"(no real handler for {name}, args={json.dumps(args)})"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        msgs = body.get("messages", [])
        tool_results = [m for m in msgs if m.get("role") == "tool"]
        # Extract the last tool name+args to build a sensible final answer.
        last_tool = ""
        for m in reversed(msgs):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                tc = m["tool_calls"][0]
                last_tool = tc.get("function", {}).get("name", "")
                break
        if not tool_results:
            # First call: request a tool. Pick based on the user prompt keywords.
            prompt = " ".join(str(m.get("content", "")) for m in msgs if m.get("role") == "user").lower()
            tool = "read_file"
            args = {"path": "sample.txt"}
            if "write" in prompt or "tulis" in prompt:
                tool, args = "write_file", {"path": "hasil.txt", "content": "konten dari agent\n"}
            elif "ls" in prompt or "list" in prompt or "jalankan" in prompt or "run" in prompt:
                tool, args = "run_command", {"command": "ls"}
            elif "cari" in prompt or "search" in prompt or "grep" in prompt:
                tool, args = "grep_file", {"pattern": "add", "path": "src"}
            elif "tidak ada" in prompt or "nok" in prompt or "empty" in prompt:
                tool, args = "read_file", {"path": "tidak-ada.txt"}
            chunk = {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {"name": tool, "arguments": json.dumps(args)},
                                }
                            ]
                        },
                    }
                ]
            }
            self._write(chunk)
        else:
            # Second call: craft a final answer that includes real tool results.
            summary = []
            for m in tool_results:
                content = m.get("content", "")
                summary.append(content)
            text = f"## Hasil tool ({last_tool})\n\n" + "\n\n".join(summary[:3]) + "\n\nSelesai."
            self._write({"choices": [{"index": 0, "delta": {"content": text}}]})
        self._write({"choices": [], "usage": {"total_tokens": 40}})
        self._write_raw(b"data: [DONE]\n\n")

    def _write(self, payload: dict) -> None:
        self._write_raw(b"data: " + json.dumps(payload).encode() + b"\n\n")

    def _write_raw(self, b: bytes) -> None:
        self.wfile.write(b)
        self.wfile.flush()

    def log_message(self, *args):  # noqa: A003
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8766), Handler).serve_forever()