"""A tiny HTTP API so other apps (Tasker, Termux:API, scripts) can call the agent.

Endpoints:
  GET  /health   -> {"ok": true, "version": ...}
  GET  /models   -> list of model ids for the provider
  GET  /config   -> effective config summary
  GET  /tools    -> registered tool specs
  GET  /agents   -> configured agent roles
  GET  /stats    -> session count + storage usage
  GET  /memory   -> persistent notes; POST /memory {content} updates them
  POST /batch    -> {prompts: [...]} runs each prompt (optional provider/model)
  POST /summarize -> {"session": id} summarizes a stored session via the agent
  POST /rerun    -> {"session": id} re-runs the session's last question
  GET  /sessions -> list saved sessions (first 50; ?note=TERM filters by note)
  GET  /sessions/<id> -> full session transcript
  GET  /sessions/<id>?markdown=1 -> transcript as markdown
  POST /sessions/<id>/note -> {note: "..."} attaches/updates the note
  DELETE /sessions/<id> -> delete a session
  POST /chat     -> {"prompt": ..., "history": [...], "agent": ..., "auto_accept": ...}
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with an optional cap on concurrent requests (0 = unlimited)."""

    def __init__(self, *args: Any, max_workers: int = 0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.daemon_threads = True
        self._sema = threading.BoundedSemaphore(max_workers) if max_workers else None

    def process_request(self, request: Any, client_address: Any) -> None:
        if self._sema:
            self._sema.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:  # noqa: BLE001
            if self._sema:
                self._sema.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            if self._sema:
                self._sema.release()
from typing import Any

from termux_agent import __version__


class _Uptime:
    def __init__(self) -> None:
        self.t0 = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.t0


_STARTED = _Uptime()


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
    max_context_tokens: int | None = None,
) -> ThreadingHTTPServer:
    _AgentHandler.build_agent = staticmethod(build_agent)
    _AgentHandler.cfg = cfg
    _AgentHandler.provider = provider
    _AgentHandler.model = model
    _AgentHandler.auto_accept = auto_accept
    _AgentHandler.token = token
    _AgentHandler.max_context_tokens = max_context_tokens
    return BoundedThreadingHTTPServer(("", 0), _AgentHandler, max_workers=0)


class _AgentHandler(BaseHTTPRequestHandler):
    server_version = f"termux-agent/{__version__}"
    build_agent = None
    cfg: dict = {}
    provider: str | None = None
    model: str | None = None
    auto_accept: bool = False
    token: str | None = None
    log_path: str | None = None
    cors_origin: str = "*"
    max_context_tokens: int | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _log_request(self, status: int, ms: float) -> None:
        if not self.log_path:
            return
        import datetime
        from pathlib import Path

        line = json.dumps(
            {
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "method": self.command,
                "path": self.path.split("?", 1)[0],
                "status": status,
                "ms": round(ms, 1),
            },
            ensure_ascii=False,
        )
        try:
            with open(Path(self.log_path).expanduser(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        import time as _time

        t0 = _time.monotonic()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", self.cors_origin)
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()
        self.wfile.write(body)
        self._log_request(code, (_time.monotonic() - t0) * 1000)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.cors_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _stream_chat(self, agent, prompt: str, session_ref, note: str | None = None) -> None:
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
        if session_id and note:
            try:
                from termux_agent.session import set_note

                set_note(session_id, note)
            except Exception:  # noqa: BLE001
                pass
        usage = getattr(agent, "usage", {}) or {}
        _sse(self, "done", {"answer": answer, "session": session_id, "usage": usage})

    def _openai_prompt(self, messages: list) -> tuple[str, list, str | None]:
        """Convert OpenAI-style messages to (prompt, history, local_image_path)."""
        history = []
        image = None
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text_parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        text_parts.append(str(part.get("text", "")))
                    elif part.get("type") == "image_url":
                        url = part.get("image_url")
                        if isinstance(url, dict):
                            url = url.get("url")
                        if isinstance(url, str) and image is None:
                            image = url
                text = "\n".join(text_parts)
            else:
                continue
            if role == "system":
                continue
            if role in ("user", "assistant") and text:
                history.append({"role": role, "content": text})
        prompt = ""
        if history:
            last = history[-1]
            prompt = last["content"]
            history = history[:-1]
        if image:
            import base64
            import tempfile
            import urllib.parse
            import urllib.request
            from pathlib import Path

            try:
                if image.startswith(("http://", "https://")):
                    with urllib.request.urlopen(image, timeout=30) as resp:
                        raw = resp.read()
                    ext = Path(urllib.parse.urlparse(image).path).suffix or ".jpg"
                elif image.startswith("data:"):
                    header, _, b64 = image.partition(",")
                    raw = base64.b64decode(b64)
                    ext = ".png" if "png" in header else ".jpg"
                else:
                    raw = None
                    ext = ""
                if raw is not None:
                    tmp_img = Path(tempfile.gettempdir()) / f"termux-agent-oai-img{ext}"
                    tmp_img.write_bytes(raw)
                    image = str(tmp_img)
            except Exception:  # noqa: BLE001
                image = None
        return prompt, history, image

    def _openai_chat(self, data: dict[str, Any]) -> None:
        """OpenAI-compatible POST /v1/chat/completions handler."""
        import uuid

        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            self._send(400, {"error": {"message": "missing 'messages' list", "type": "invalid_request_error"}})
            return
        model = str(data.get("model") or self.model or "")
        stream = bool(data.get("stream"))
        prompt, history, image = self._openai_prompt(messages)
        if not prompt:
            self._send(400, {"error": {"message": "no user message found", "type": "invalid_request_error"}})
            return
        mct = data.get("max_context_tokens")
        if mct is None:
            mct = data.get("max_tokens")
        if mct is None:
            mct = self.max_context_tokens
        try:
            agent = self.build_agent(
                self.cfg,
                data.get("provider") or self.provider,
                model or None,
                auto_accept=True,
                temperature=float(data["temperature"]) if isinstance(data.get("temperature"), (int, float)) else None,
                max_tool_rounds=int(data["max_tool_rounds"]) if isinstance(data.get("max_tool_rounds"), int) else None,
                max_context_tokens=int(mct) if isinstance(mct, int) else None,
                only_tools=[t for t in data.get("only_tools") if isinstance(t, str)] if isinstance(data.get("only_tools"), list) else None,
                max_output_chars=int(data["max_output_chars"]) if isinstance(data.get("max_output_chars"), int) else None,
                command_timeout=int(data["command_timeout"]) if isinstance(data.get("command_timeout"), int) else None,
                disabled_groups=[g for g in data.get("disabled_groups") if isinstance(g, str)] if isinstance(data.get("disabled_groups"), list) else None,
                retries=int(data["retries"]) if isinstance(data.get("retries"), int) else None,
                no_fallback=bool(data.get("no_fallback")),
                allow_dirs=[d for d in data.get("allow_dirs") if isinstance(d, str)] if isinstance(data.get("allow_dirs"), list) else None,
                readonly=bool(data.get("readonly")),
                memory=bool(data.get("memory", True)),
            )
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": {"message": str(e), "type": "server_error"}})
            return
        if history:
            agent.messages = [agent.messages[0]] + history
        if image:
            prompt += f"\n[image: {image}]"
        cid = f"chatcmpl-{uuid.uuid4().hex}"
        if stream:
            import time as _time

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", self.cors_origin)
            self.end_headers()

            def _chunk(delta: dict, finish: str | None = None) -> None:
                obj = {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": int(_time.time()),
                    "model": model or agent.provider.model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n")
                self.wfile.flush()

            _chunk({"role": "assistant"})
            try:
                answer = agent.run(prompt, on_text_delta=lambda d: _chunk({"content": d}))
            except Exception:  # noqa: BLE001
                _chunk({}, finish="stop")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            session_id = ""
            try:
                from termux_agent.session import record_messages

                session_id = record_messages(
                    agent.messages,
                    agent.provider.name,
                    agent.provider.model,
                    session_id=None,
                )
            except Exception:  # noqa: BLE001
                pass
            note = data.get("note")
            if session_id and isinstance(note, str) and note.strip():
                try:
                    from termux_agent.session import set_note

                    set_note(session_id, note.strip())
                except Exception:  # noqa: BLE001
                    pass
            self.wfile.write(
                b"data: "
                + json.dumps(
                    {"id": cid, "object": "chat.completion.chunk", "created": int(_time.time()), "model": model or agent.provider.model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "session": session_id},
                    ensure_ascii=False,
                ).encode("utf-8")
                + b"\n\n"
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        answer = agent.run(prompt)
        usage = getattr(agent, "usage", {}) or {}
        session_id = ""
        try:
            from termux_agent.session import record_messages

            session_id = record_messages(
                agent.messages,
                agent.provider.name,
                agent.provider.model,
                session_id=None,
            )
        except Exception:  # noqa: BLE001
            pass
        note = data.get("note")
        if session_id and isinstance(note, str) and note.strip():
            try:
                from termux_agent.session import set_note

                set_note(session_id, note.strip())
            except Exception:  # noqa: BLE001
                pass
        self._send(
            200,
            {
                "id": cid,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model or agent.provider.model,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                    "completion_tokens": int(usage.get("completion_tokens", 0)),
                    "total_tokens": int(usage.get("total_tokens", 0)),
                },
                "session": session_id,
            },
        )

    def _summarize(self, data: dict[str, Any]) -> None:
        """POST /summarize {"session": id} -> {summary} using the agent."""
        from termux_agent.cli import _run_guarded
        from termux_agent.session import export_session

        sid = str(data.get("session", "")).strip()
        if not sid:
            self._send(400, {"ok": False, "error": "missing 'session'"})
            return
        try:
            sdata = export_session(sid)
        except FileNotFoundError:
            self._send(404, {"ok": False, "error": "session not found"})
            return
        transcript = []
        for m in sdata.get("messages", []):
            role = m.get("role", "?")
            if role == "system":
                continue
            content = str(m.get("content", ""))[:2000]
            if content.strip():
                transcript.append(f"{role.upper()}: {content}")
        if not transcript:
            self._send(400, {"ok": False, "error": "session has no usable messages"})
            return
        prompt = (
            "Summarize the following conversation in a clear, structured way: "
            "main topic, decisions, files/commands touched, and open questions. "
            "Keep it under 200 words.\n\n"
            + "\n\n".join(transcript)
        )
        try:
            agent = self.build_agent(self.cfg, self.provider, self.model, auto_accept=True)
            summary = _run_guarded(agent, prompt, lambda *a, **k: None, None)
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": str(e)})
            return
        self._send(200, {"ok": True, "session": sdata.get("id"), "summary": summary})

    def _rerun(self, data: dict[str, Any]) -> None:
        """POST /rerun {"session": id} -> {answer} for the session's last question."""
        from termux_agent.cli import _run_guarded
        from termux_agent.session import export_session

        sid = str(data.get("session", "")).strip()
        if not sid:
            self._send(400, {"ok": False, "error": "missing 'session'"})
            return
        try:
            sdata = export_session(sid)
        except FileNotFoundError:
            self._send(404, {"ok": False, "error": "session not found"})
            return
        last_user = next(
            (str(m.get("content", "")) for m in reversed(sdata.get("messages", [])) if m.get("role") == "user"),
            "",
        )
        if not last_user.strip():
            self._send(400, {"ok": False, "error": "session has no user prompt"})
            return
        old_answer = next(
            (str(m.get("content", "")) for m in reversed(sdata.get("messages", [])) if m.get("role") == "assistant"),
            "",
        )
        try:
            agent = self.build_agent(self.cfg, self.provider, self.model, auto_accept=True)
            answer = _run_guarded(agent, last_user, lambda *a, **k: None, None)
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": str(e)})
            return
        self._send(200, {"ok": True, "session": sdata.get("id"), "prompt": last_user, "answer": answer, "old": old_answer})

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = (
                f"<!doctype html><html><head><meta charset=utf-8><title>termux-agent {__version__}</title></head>"
                "<body><h1>termux-agent server</h1>"
                f"<p>version <code>{__version__}</code> · pid <code>{__import__('os').getpid()}</code></p>"
                "<ul>"
                "<li><code>GET /health</code> – version, pid, uptime</li>"
                "<li><code>GET /models?provider=X</code> – provider models</li>"
                "<li><code>GET /config</code> – effective config</li>"
                "<li><code>GET /tools</code> – registered tool specs</li>"
                "<li><code>GET /agents</code> – agent roles</li>"
                "<li><code>GET /stats</code> – session stats</li>"
                "<li><code>GET /memory</code> / <code>POST /memory</code> – persistent notes</li>"
                "<li><code>GET /sessions[?limit=N]</code> – saved sessions</li>"
                "<li><code>GET /sessions/&lt;id&gt;[?markdown=1]</code> / <code>DELETE</code> – session access</li>"
                "<li><code>POST /chat</code> – run a prompt (JSON body; <code>stream: true</code> for SSE)</li>"
                "<li><code>POST /batch</code> – run a list of prompts in parallel</li>"
                "<li><code>POST /summarize</code> – summarize a stored session</li>"
                "<li><code>POST /rerun</code> – re-run a stored session's last question</li>"
                "</ul></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self._log_request(200, 0)
            return
        if self.path == "/health":
            import os

            self._send(
                200,
                {"ok": True, "version": __version__, "pid": os.getpid(), "uptime": round(_STARTED.elapsed(), 1)},
            )
        elif self.path.split("?", 1)[0] in ("/models", "/v1/models", "/sessions", "/config", "/tools", "/agents", "/stats", "/memory"):
            if not _authorized(self):
                _send_unauthorized(self)
                return
            if self.path == "/memory":
                from termux_agent.agent import load_memory

                self._send(200, {"memory": load_memory()})
            elif self.path == "/stats":
                from termux_agent.session import SESSIONS_DIR, list_sessions

                sess = list_sessions()
                total = sum(s.stat().st_size for s in sess)
                self._send(
                    200,
                    {
                        "sessions": len(sess),
                        "sessions_bytes": total,
                        "provider": self.provider or self.cfg.get("provider", "zen"),
                        "agent": self.cfg.get("agent", "root"),
                        "api_version": 1,
                    },
                )
            elif self.path == "/agents":
                self._send(
                    200,
                    {
                        "agents": [
                            {"name": n, "description": spec.get("description", ""), "tools": spec.get("tools") or []}
                            for n, spec in self.cfg.get("agents", {}).items()
                        ]
                    },
                )
            elif self.path == "/tools":
                from termux_agent.tools.base import tool_specs

                self._send(200, {"tools": [{"name": s.name, "description": s.description} for s in tool_specs()]})
            elif self.path.split("?", 1)[0] in ("/models", "/v1/models"):
                import urllib.parse

                q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                prov = (q.get("provider") or [None])[0] or self.provider
                try:
                    provider = self.build_agent(self.cfg, prov, self.model, auto_accept=True)
                    live = provider.provider.list_models()
                    models = live or [m for m in (self.cfg.get("providers", {}).get(prov or self.cfg.get("provider", "zen"), {}).get("models") or [])]
                    if self.path.split("?", 1)[0] == "/v1/models":
                        self._send(
                            200,
                            {
                                "object": "list",
                                "data": [{"id": m, "object": "model", "owned_by": prov} for m in models],
                            },
                        )
                    else:
                        self._send(200, {"provider": prov, "models": models})
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
                from termux_agent.session import all_notes, list_sessions, read_session

                import urllib.parse

                q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                try:
                    limit = max(1, min(int(q.get("limit", ["50"])[0]), 500))
                except ValueError:
                    limit = 50
                note_filter = (q.get("note", [""])[0] or "").strip()
                notes = all_notes()
                sessions = []
                for s in list_sessions()[:limit]:
                    note = notes.get(s.stem, "")
                    if note_filter and note_filter not in note:
                        continue
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
                            "note": note[:100],
                        }
                    )
                self._send(200, {"sessions": sessions})
        elif self.path.split("?", 1)[0].startswith("/sessions/"):
            if not _authorized(self):
                _send_unauthorized(self)
                return
            sid = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
            try:
                from termux_agent.session import export_session

                data = export_session(sid)
                if "?" in self.path and "markdown=1" in self.path:
                    from termux_agent.cli import _session_to_markdown

                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Length", str(len(_session_to_markdown(data).encode("utf-8"))))
                    self.end_headers()
                    self.wfile.write(_session_to_markdown(data).encode("utf-8"))
                    self._log_request(200, 0)
                else:
                    self._send(200, data)
            except FileNotFoundError:
                self._send(404, {"ok": False, "error": "session not found"})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        data = _read_body(self)
        if not _authorized(self, data):
            _send_unauthorized(self)
            return
        if self.path == "/memory":
            content = data.get("content")
            if not isinstance(content, str):
                self._send(400, {"ok": False, "error": "missing 'content'"})
                return
            from termux_agent.agent import MEMORY_FILE

            MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            MEMORY_FILE.write_text(content.strip(), encoding="utf-8")
            self._send(200, {"ok": True, "memory": content.strip()})
            return
        if self.path == "/batch":
            prompts = data.get("prompts")
            if not isinstance(prompts, list) or not all(isinstance(p, str) and p.strip() for p in prompts):
                self._send(400, {"ok": False, "error": "missing non-empty 'prompts' list"})
                return
            provider = str(data.get("provider") or self.provider or self.cfg.get("provider", "zen"))
            model = str(data.get("model") or self.model or "")
            only_tools = [t for t in data.get("only_tools") if isinstance(t, str)] if isinstance(data.get("only_tools"), list) else None
            disabled_groups = [g for g in data.get("disabled_groups") if isinstance(g, str)] if isinstance(data.get("disabled_groups"), list) else None
            allow_dirs = [d for d in data.get("allow_dirs") if isinstance(d, str)] if isinstance(data.get("allow_dirs"), list) else None
            temp = data.get("temperature")
            mtr = data.get("max_tool_rounds")
            mct = data.get("max_context_tokens")
            if mct is None:
                mct = self.max_context_tokens
            from concurrent.futures import ThreadPoolExecutor

            def _one(p: str) -> dict:
                try:
                    agent = self.build_agent(
                        self.cfg,
                        provider,
                        model,
                        auto_accept=True,
                        temperature=float(temp) if isinstance(temp, (int, float)) else None,
                        max_tool_rounds=int(mtr) if isinstance(mtr, int) else None,
                        max_context_tokens=int(mct) if isinstance(mct, int) else None,
                        only_tools=only_tools,
                        max_output_chars=int(data["max_output_chars"]) if isinstance(data.get("max_output_chars"), int) else None,
                        command_timeout=int(data["command_timeout"]) if isinstance(data.get("command_timeout"), int) else None,
                        disabled_groups=disabled_groups,
                        retries=int(data["retries"]) if isinstance(data.get("retries"), int) else None,
                        no_fallback=bool(data.get("no_fallback")),
                        allow_dirs=allow_dirs,
                        readonly=bool(data.get("readonly")),
                        memory=bool(data.get("memory", True)),
                    )
                    answer = agent.run(p)
                    return {"prompt": p, "answer": answer}
                except Exception as e:  # noqa: BLE001
                    return {"prompt": p, "answer": None, "error": str(e)}

            results = list(ThreadPoolExecutor(max_workers=4).map(_one, [p.strip() for p in prompts]))
            self._send(200, {"results": results})
            return
        if self.path.startswith("/sessions/") and self.path.rstrip("/").endswith("/note"):
            from termux_agent.session import set_note

            base = self.path.rstrip("/")[: -len("/note")]
            sid = base.rsplit("/", 1)[-1]
            note = data.get("note")
            if not isinstance(note, str):
                self._send(400, {"ok": False, "error": "missing string 'note'"})
                return
            try:
                from termux_agent.session import list_sessions

                if not any(s.stem == sid for s in list_sessions()):
                    raise FileNotFoundError
                set_note(sid, note.strip())
            except FileNotFoundError:
                self._send(404, {"ok": False, "error": "session not found"})
                return
            self._send(200, {"ok": True, "session": sid, "note": note.strip()})
            return
        if self.path in ("/v1/chat/completions", "/chat/completions"):
            self._openai_chat(data)
            return
        if self.path == "/summarize":
            self._summarize(data)
            return
        if self.path == "/rerun":
            self._rerun(data)
            return
        if self.path != "/chat":
            self._send(404, {"ok": False, "error": "not found"})
            return
        prompt = str(data.get("prompt", "")).strip()
        if not prompt:
            self._send(400, {"ok": False, "error": "missing 'prompt'"})
            return
        image = data.get("image")
        if isinstance(image, str) and image:
            if image.startswith(("http://", "https://")):
                import tempfile
                import urllib.parse
                import urllib.request
                from pathlib import Path

                try:
                    with urllib.request.urlopen(image, timeout=30) as resp:
                        raw = resp.read()
                    ext = urllib.parse.urlparse(image).path
                    ext = Path(ext).suffix or ".jpg"
                    tmp_img = Path(tempfile.gettempdir()) / f"termux-agent-img{ext}"
                    tmp_img.write_bytes(raw)
                    image = str(tmp_img)
                except Exception as e:  # noqa: BLE001
                    self._send(400, {"ok": False, "error": f"failed to download image: {e}"})
                    return
            from os.path import exists

            if exists(image):
                prompt += f"\n[image: {image}]"
            else:
                self._send(400, {"ok": False, "error": f"image not found: {image}"})
                return
        try:
            temp = data.get("temperature")
            mtr = data.get("max_tool_rounds")
            mct = data.get("max_context_tokens")
            if mct is None:
                mct = self.max_context_tokens
            agent = self.build_agent(
                self.cfg,
                data.get("provider") or self.provider,
                data.get("model") or self.model,
                auto_accept=bool(data.get("auto_accept", self.auto_accept)),
                agent_name=data.get("agent"),
                working_dir=data.get("cwd"),
                extra_rules=data.get("rules"),
                system_prompt=data.get("system_prompt") or None,
                temperature=float(temp) if isinstance(temp, (int, float)) else None,
                max_tool_rounds=int(mtr) if isinstance(mtr, int) else None,
                max_context_tokens=int(mct) if isinstance(mct, int) else None,
                only_tools=[t for t in data.get("only_tools") if isinstance(t, str)] if isinstance(data.get("only_tools"), list) else None,
                max_output_chars=int(data["max_output_chars"]) if isinstance(data.get("max_output_chars"), int) else None,
                command_timeout=int(data["command_timeout"]) if isinstance(data.get("command_timeout"), int) else None,
                disabled_groups=[g for g in data.get("disabled_groups") if isinstance(g, str)] if isinstance(data.get("disabled_groups"), list) else None,
                retries=int(data["retries"]) if isinstance(data.get("retries"), int) else None,
                no_fallback=bool(data.get("no_fallback")),
                allow_dirs=[d for d in data.get("allow_dirs") if isinstance(d, str)] if isinstance(data.get("allow_dirs"), list) else None,
                readonly=bool(data.get("readonly")),
                memory=bool(data.get("memory", True)),
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
            note = data.get("note")
            self._stream_chat(agent, prompt, session_ref, note=str(note).strip() if isinstance(note, str) else None)
            return
        answer = agent.run(prompt)
        if data.get("notify"):
            from termux_agent.notify import notify as _notify

            try:
                _notify(f"Chat done: {answer[:120]}")
            except Exception:  # noqa: BLE001
                pass
        from termux_agent.session import record_messages

        session_id = record_messages(
            agent.messages,
            agent.provider.name,
            agent.provider.model,
            session_id=session_ref if (isinstance(session_ref, str) and session_ref) else None,
        )
        note = data.get("note")
        if isinstance(note, str) and note.strip():
            from termux_agent.session import set_note

            set_note(session_id, note.strip())
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

    def do_DELETE(self) -> None:
        if not _authorized(self):
            _send_unauthorized(self)
            return
        path = self.path.split("?", 1)[0]
        if not path.startswith("/sessions/"):
            self._send(404, {"ok": False, "error": "not found"})
            return
        sid = path.rsplit("/", 1)[-1]
        if not sid:
            self._send(404, {"ok": False, "error": "not found"})
            return
        from termux_agent.session import delete_session

        removed = delete_session(sid)
        if removed is None:
            self._send(404, {"ok": False, "error": "session not found"})
            return
        self._send(200, {"ok": True, "deleted": removed.stem})


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8787, provider: str | None = None, model: str | None = None, auto_accept: bool = False, token: str | None = None, log_file: str | None = None, cors_origin: str = "*", tls_cert: str | None = None, tls_key: str | None = None, max_workers: int = 0, max_context_tokens: int | None = None) -> int:
    from termux_agent.cli import build_agent as _build

    handler = _AgentHandler
    handler.build_agent = staticmethod(_build)
    handler.cfg = cfg
    handler.provider = provider
    handler.model = model
    handler.auto_accept = auto_accept
    handler.token = token
    handler.log_path = log_file
    handler.cors_origin = cors_origin
    handler.max_context_tokens = max_context_tokens
    httpd = BoundedThreadingHTTPServer((host, port), handler, max_workers=max_workers)
    scheme = "http"
    if tls_cert:
        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls_cert, tls_key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    import sys

    print(f"termux-agent server listening on {scheme}://{host}:{httpd.server_address[1]}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0