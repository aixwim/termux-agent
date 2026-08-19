"""A tiny HTTP API so other apps (Tasker, Termux:API, scripts) can call the agent.

Endpoints:
  GET  /health   -> {"ok": true, "version": ...}
  GET  /models   -> list of model ids for the provider
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

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True, "version": __version__})
        elif self.path in ("/models", "/sessions"):
            if not _authorized(self):
                _send_unauthorized(self)
                return
            if self.path == "/models":
                try:
                    provider = self.build_agent(self.cfg, self.provider, self.model, auto_accept=True)
                    live = provider.provider.list_models()
                    models = live or [m for m in (self.cfg.get("providers", {}).get(self.provider or self.cfg.get("provider", "zen"), {}).get("models") or [])]
                    self._send(200, {"models": models})
                except Exception as e:  # noqa: BLE001
                    self._send(500, {"ok": False, "error": str(e)})
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
        try:
            agent = self.build_agent(
                self.cfg,
                self.provider,
                self.model,
                auto_accept=bool(data.get("auto_accept", self.auto_accept)),
                agent_name=data.get("agent"),
                working_dir=data.get("cwd"),
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