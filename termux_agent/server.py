"""A tiny HTTP API so other apps (Tasker, Termux:API, scripts) can call the agent.

Endpoints:
  GET  /health   -> {"ok": true, "version": ...}
  GET  /models   -> list of model ids for the provider
  GET  /config   -> effective config summary
  GET  /tools    -> registered tool specs
  GET  /sessions -> list saved sessions (first 50)
  GET  /sessions/<id> -> full session transcript
  POST /chat     -> {"prompt": ..., "history": [...], "agent": ..., "auto_accept": ...}
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from termux_agent import __version__


def _read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _authorized(handler: BaseHTTPRequestHandler, body: dict[str, Any] | None = None) -> bool:
    """Require a bearer token when the server was started with --token."""
    token = getattr(handler, "token", None)
    if not token:
        return True
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        supplied = auth[len("Bearer ") :].strip()
    elif body and isinstance(body.get("token"), str):
        supplied = body["token"].strip()
    else:
        supplied = ""
    return bool(supplied) and supplied == token


def _send_unauthorized(handler: BaseHTTPRequestHandler) -> None:
    body = json.dumps({"ok": False, "error": "unauthorized"}).encode("utf-8")
    handler.send_response(401)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _sse(handler: BaseHTTPRequestHandler, event: str, data: dict[str, Any]) -> None:
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.wfile.write(b"event: " + event.encode() + b"\ndata: " + payload + b"\n\n")
    handler.wfile.flush()


def build_server(
    build_agent,
    cfg: dict,
    provider: str | None,
    model: str | None,
    auto_accept: bool = False,
    token: str | None = None,
) -> ThreadingHTTPServer:
    _AgentHandler.build_agent = staticmethod(build_agent)
    _AgentHandler.cfg = cfg
    _AgentHandler.provider = provider
    _AgentHandler.model = model
    _AgentHandler.auto_accept = auto_accept
    _AgentHandler.token = token
    return ThreadingHTTPServer(("", 0), _AgentHandler)


class _AgentHandler(BaseHTTPRequestHandler):
    server_version = f"termux-agent/{__version__}"
    build_agent = None
    cfg: dict = {}
    provider: str | None = None
    model: str | None = None
    auto_accept: bool = False
    token: str | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _stream_chat(self, agent, prompt: str, session_ref) -> None:
        """Stream POST /chat responses as Server-Sent Events."""
        self.send_response(200)
        _sse(self, "start", {"provider": agent.provider.name, "model": agent.provider.model})
        try:
            answer = agent.run(
                prompt,
                on_text_delta=lambda d: _sse(self, "delta", {"text": d}),
                on_tool_use=lambda n, a: _sse(self, "tool", {"name": n, "arguments": a}),
            )
        except Exception as e:  # noqa: BLE001
            _sse(self, "error", {"error": str(e)})
            return
        try:
            from termux_agent.session import record_messages

            session_id = record_messages(
                agent.messages,
                agent.provider.name,
                agent.provider.model,
                session_id=session_ref if (isinstance(session_ref, str) and session_ref) else None,
            )
        except Exception:  # noqa: BLE001
            session_id = ""
        usage = getattr(agent, "usage", {}) or {}
        _sse(self, "done", {"answer": answer, "session": session_id, "usage": usage})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True, "version": __version__})
        elif self.path in ("/models", "/sessions", "/config", "/tools"):
            if not _authorized(self):
                _send_unauthorized(self)
                return
            if self.path == "/tools":
                from termux_agent.tools.base import tool_specs

                self._send(200, {"tools": [{"name": s.name, "description": s.description} for s in tool_specs()]})
            elif self.path == "/models":
                try:
                    provider = self.build_agent(self.cfg, self.provider, self.model, auto_accept=True)
                    live = provider.provider.list_models()
                    models = live or [m for m in (self.cfg.get("providers", {}).get(self.provider or self.cfg.get("provider", "zen"), {}).get("models") or [])]
                    self._send(200, {"models": models})
                except Exception as e:  # noqa: BLE001
                    self._send(500, {"ok": False, "error": str(e)})
            elif self.path == "/config":
                self._send(
                    200,
                    {
                        "provider": self.provider or self.cfg.get("provider", "zen"),
                        "model": self.model or "",
                        "agent": self.cfg.get("agent", "root"),
                        "temperature": self.cfg.get("temperature", 0.7),
                        "max_tool_rounds": self.cfg.get("max_tool_rounds", 20),
                        "max_context_tokens": self.cfg.get("max_context_tokens", 0),
                        "max_output_chars": self.cfg.get("max_output_chars", 60000),
                        "command_timeout": self.cfg.get("command_timeout", 60),
                        "confirm_commands": self.cfg.get("confirm_commands", True),
                        "allow_storage": self.cfg.get("allow_storage", False),
                    },
                )
            else:
                from termux_agent.session import list_sessions, read_session

                sessions = []
                for s in list_sessions()[:50]:
                    recs = read_session(s)
                    info = next((r for r in recs if r.get("provider")), {})
                    first_user = next((r["content"] for r in recs if r.get("role") == "user" and r.get("content")), "")
                    sessions.append(
                        {
                            "id": s.stem,
                            "provider": info.get("provider") or "",
                            "model": info.get("model") or "",
                            "messages": len(recs),
                            "first": str(first_user)[:100],
                        }
                    )
                self._send(200, {"sessions": sessions})
        elif self.path.startswith("/sessions/"):
            if not _authorized(self):
                _send_unauthorized(self)
                return
            sid = self.path.rsplit("/", 1)[-1]
            try:
                from termux_agent.session import export_session

                self._send(200, export_session(sid))
            except FileNotFoundError:
                self._send(404, {"ok": False, "error": "session not found"})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/chat":
            self._send(404, {"ok": False, "error": "not found"})
            return
        data = _read_body(self)
        if not _authorized(self, data):
            _send_unauthorized(self)
            return
        prompt = str(data.get("prompt", "")).strip()
        if not prompt:
            self._send(400, {"ok": False, "error": "missing 'prompt'"})
            return
        image = data.get("image")
        if isinstance(image, str) and image:
            from os.path import exists

            if exists(image):
                prompt += f"\n[image: {image}]"
            else:
                self._send(400, {"ok": False, "error": f"image not found: {image}"})
                return
        try:
            agent = self.build_agent(
                self.cfg,
                data.get("provider") or self.provider,
                data.get("model") or self.model,
                auto_accept=bool(data.get("auto_accept", self.auto_accept)),
                agent_name=data.get("agent"),
                working_dir=data.get("cwd"),
                extra_rules=data.get("rules"),
            )
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": str(e)})
            return
        history = data.get("history")
        if isinstance(history, list):
            seeded = []
            for m in history:
                if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content"):
                    seeded.append({"role": m["role"], "content": str(m["content"])})
            if seeded:
                agent.messages = [agent.messages[0]] + seeded
        session_ref = data.get("session")
        if isinstance(session_ref, str) and session_ref:
            from termux_agent.session import list_sessions, session_messages

            matches = [s for s in list_sessions() if s.stem.startswith(session_ref)]
            if matches:
                agent.messages = [agent.messages[0]] + session_messages(matches[-1])
        if data.get("stream"):
            self._stream_chat(agent, prompt, session_ref)
            return
        answer = agent.run(prompt)
        from termux_agent.session import record_messages

        session_id = record_messages(
            agent.messages,
            agent.provider.name,
            agent.provider.model,
            session_id=session_ref if (isinstance(session_ref, str) and session_ref) else None,
        )
        usage = getattr(agent, "usage", {}) or {}
        self._send(
            200,
            {
                "ok": True,
                "answer": answer,
                "provider": agent.provider.name,
                "model": agent.provider.model,
                "session": session_id,
                "usage": usage,
            },
        )


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8787, provider: str | None = None, model: str | None = None, auto_accept: bool = False, token: str | None = None) -> int:
    from termux_agent.cli import build_agent as _build

    handler = _AgentHandler
    handler.build_agent = staticmethod(_build)
    handler.cfg = cfg
    handler.provider = provider
    handler.model = model
    handler.auto_accept = auto_accept
    handler.token = token
    httpd = ThreadingHTTPServer((host, port), handler)
    import sys

    print(f"termux-agent server listening on http://{host}:{httpd.server_address[1]}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0