import json
import queue
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .base import AgentResponse, ChannelMessage, ChannelSendResult
from .runtime import ChannelRuntime
from .types import ChannelKind, TrustLevel


class WebChannel:
    def __init__(
        self,
        channel_runtime: ChannelRuntime,
        channel_id: str = "web",
        host: str = "127.0.0.1",
        port: int = 9528,
        auth_token: str = "",
        permission_timeout_seconds: int = 300,
        llm_control=None,
        memory_admin=None,
    ):
        self.channel_runtime = channel_runtime
        self.channel_id = channel_id
        self.kind = ChannelKind.WEB
        self.trust_level = TrustLevel.HIGH
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.permission_timeout_seconds = permission_timeout_seconds
        self.llm_control = llm_control
        self.memory_admin = memory_admin
        self.queues: dict[str, queue.Queue[dict]] = {}
        self.request_sessions: dict[str, str] = {}
        self.conversation_requests: dict[str, str] = {}
        self.permission_replies: dict[str, queue.Queue[bool]] = {}
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._local = threading.local()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        if self._server is not None:
            return
        handler = self._make_handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="aniyaagent-web-channel",
        )
        self._server_thread.start()

    def serve_forever(self) -> None:
        if self._server is None:
            handler = self._make_handler()
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.serve_forever()

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        self._server = None

    def submit_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or payload.get("content") or payload.get("message") or "").strip()
        if not text:
            return {"ok": False, "error": "Message text is required."}

        # Desktop and mobile Web are one local user's continuous conversation.
        conversation_id = "personal"
        user_id = "local"
        request_id = uuid.uuid4().hex
        event_queue: queue.Queue[dict] = queue.Queue()

        with self._lock:
            self.queues[request_id] = event_queue
            self.request_sessions[request_id] = conversation_id
            self.conversation_requests[conversation_id] = request_id

        message = ChannelMessage(
            channel_id=self.channel_id,
            user_id=user_id,
            conversation_id=conversation_id,
            text=text,
            kind=ChannelKind.WEB,
            trust_level=TrustLevel.HIGH,
            files=list(payload.get("files") or []),
            images=list(payload.get("images") or []),
            metadata=dict(payload.get("metadata") or {}),
        )
        threading.Thread(
            target=self._run_request,
            args=(request_id, message),
            daemon=True,
            name=f"web-channel-request-{request_id[:8]}",
        ).start()
        return {
            "ok": True,
            "request_id": request_id,
            "conversation_id": conversation_id,
            "stream_url": f"/stream?request_id={request_id}",
        }

    def send(self, response: AgentResponse) -> ChannelSendResult:
        request_id = self.conversation_requests.get(response.conversation_id)
        if not request_id:
            # In-app delivery is only durable when a notification store records it.
            return ChannelSendResult(False, "no active Web stream; not a durable delivery target")
        self._enqueue(
            request_id,
            {
                "type": "response",
                "conversation_id": response.conversation_id,
                "run_id": response.run_id,
                "status": response.status,
                "content": response.text,
                "error": response.error,
                "metadata": response.metadata,
            },
        )
        return ChannelSendResult(True, "queued")

    def ask_user(self, block, reason: str) -> bool:
        request_id = getattr(self._local, "request_id", "")
        if not request_id:
            return False

        permission_id = f"perm_{uuid.uuid4().hex[:12]}"
        reply_queue: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.permission_replies[permission_id] = reply_queue
        self._enqueue(
            request_id,
            {
                "type": "permission_request",
                "request_id": permission_id,
                "tool": str(getattr(block, "name", "")),
                "reason": reason,
                "input": dict(getattr(block, "input", {}) or {}),
            },
        )
        try:
            return bool(reply_queue.get(timeout=self.permission_timeout_seconds))
        except queue.Empty:
            return False
        finally:
            self.permission_replies.pop(permission_id, None)

    def answer_permission(self, permission_id: str, allow: bool) -> bool:
        reply_queue = self.permission_replies.get(permission_id)
        if reply_queue is None:
            return False
        reply_queue.put(bool(allow))
        return True

    def stream(self, request_id: str):
        event_queue = self.queues.get(request_id)
        if event_queue is None:
            yield {"type": "error", "error": f"Unknown request_id: {request_id}"}
            return

        while True:
            try:
                event = event_queue.get(timeout=15)
            except queue.Empty:
                yield {"type": "ping", "time": time.time()}
                continue
            yield event
            if event.get("type") in {"done", "error"}:
                with self._lock:
                    conversation_id = self.request_sessions.pop(request_id, "")
                    if conversation_id and self.conversation_requests.get(conversation_id) == request_id:
                        self.conversation_requests.pop(conversation_id, None)
                    self.queues.pop(request_id, None)
                return

    def _run_request(self, request_id: str, message: ChannelMessage) -> None:
        self._local.request_id = request_id
        self._enqueue(request_id, {"type": "accepted", "request_id": request_id, "conversation_id": message.conversation_id})
        try:
            response = self.channel_runtime.handle_message(
                message,
                deliver=False,
                event_callback=lambda event_type, payload: self._on_runtime_event(request_id, event_type, payload),
            )
            self._enqueue(
                request_id,
                {
                    "type": "done",
                    "request_id": request_id,
                    "conversation_id": message.conversation_id,
                    "run_id": response.run_id,
                    "status": response.status,
                    "content": response.text,
                    "error": response.error,
                    "metadata": response.metadata,
                },
            )
        except Exception as exc:
            self._enqueue(
                request_id,
                {
                    "type": "error",
                    "request_id": request_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                },
            )
        finally:
            self._local.request_id = ""

    def _on_runtime_event(self, request_id: str, event_type: str, payload: dict | None) -> None:
        self._enqueue(request_id, self._map_runtime_event(event_type, payload or {}))

    def _map_runtime_event(self, event_type: str, payload: dict) -> dict:
        mapped_type = {
            "loop.turn.started": "phase",
            "loop.turn.completed": "phase",
            "loop.turn.failed": "phase",
            "llm.request.started": "llm_start",
            "llm.request.completed": "llm_end",
            "llm.request.failed": "llm_error",
            "tool.call.started": "tool_start",
            "tool.call.completed": "tool_end",
            "tool.call.blocked": "tool_blocked",
            "checkpoint.saved": "checkpoint",
        }.get(event_type, "event")
        return {"type": mapped_type, "event": event_type, "data": payload}

    def _enqueue(self, request_id: str, event: dict) -> None:
        event_queue = self.queues.get(request_id)
        if event_queue is not None:
            event_queue.put(event)

    def _make_handler(self):
        channel = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_OPTIONS(self):
                self._send_empty(204)

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._send_json({"ok": True, "channel": channel.channel_id})
                    return
                if parsed.path == "/channels":
                    if not self._authorized():
                        self._send_json({"ok": False, "error": "Unauthorized"}, status=401)
                        return
                    self._send_json({"ok": True, "channels": channel.channel_runtime.registry.list_channels()})
                    return
                if parsed.path == "/llm/providers":
                    if not self._authorized():
                        self._send_json({"ok": False, "error": "Unauthorized"}, status=401)
                        return
                    if channel.llm_control is None:
                        self._send_json({"ok": False, "error": "LLM provider control is unavailable"}, status=503)
                        return
                    self._send_json({"ok": True, **channel.llm_control.list_providers()})
                    return
                if parsed.path == "/memory/messages":
                    if not self._authorized() or channel.memory_admin is None:
                        self._send_json({"ok": False, "error": "Memory API unavailable"}, status=503)
                        return
                    query = parse_qs(parsed.query)
                    self._send_json({"ok": True, "messages": channel.memory_admin.factual_messages(query.get("date", [""])[0], int(query.get("limit", ["100"])[0]))})
                    return
                if parsed.path == "/memory/daily":
                    if not self._authorized() or channel.memory_admin is None:
                        self._send_json({"ok": False, "error": "Memory API unavailable"}, status=503)
                        return
                    query = parse_qs(parsed.query)
                    self._send_json({"ok": True, "daily": channel.memory_admin.daily_memory(query.get("date", [""])[0]), "days": channel.memory_admin.daily_memories()})
                    return
                if parsed.path == "/memory/long-term":
                    if not self._authorized() or channel.memory_admin is None:
                        self._send_json({"ok": False, "error": "Memory API unavailable"}, status=503)
                        return
                    query = parse_qs(parsed.query)
                    self._send_json({"ok": True, "memories": channel.memory_admin.long_term_memories(query.get("status", [""])[0])})
                    return
                if parsed.path == "/memory/export":
                    if not self._authorized() or channel.memory_admin is None:
                        self._send_json({"ok": False, "error": "Memory API unavailable"}, status=503)
                        return
                    self._send_json({"ok": True, "messages": channel.memory_admin.retention.export()})
                    return
                if parsed.path == "/notifications":
                    if not self._authorized() or channel.memory_admin is None:
                        self._send_json({"ok": False, "error": "Notification API unavailable"}, status=503)
                        return
                    self._send_json({"ok": True, "notifications": channel.memory_admin.notification_status()})
                    return
                if parsed.path == "/stream":
                    if not self._authorized():
                        self._send_json({"ok": False, "error": "Unauthorized"}, status=401)
                        return
                    request_id = parse_qs(parsed.query).get("request_id", [""])[0]
                    self._send_sse(request_id)
                    return
                self._send_json({"ok": False, "error": "Not found"}, status=404)

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path == "/message":
                    if not self._authorized():
                        self._send_json({"ok": False, "error": "Unauthorized"}, status=401)
                        return
                    self._send_json(channel.submit_message(self._read_json()))
                    return
                if parsed.path == "/permission":
                    if not self._authorized():
                        self._send_json({"ok": False, "error": "Unauthorized"}, status=401)
                        return
                    payload = self._read_json()
                    permission_id = str(payload.get("request_id") or payload.get("permission_id") or "")
                    ok = channel.answer_permission(permission_id, bool(payload.get("allow")))
                    self._send_json({"ok": ok})
                    return
                if parsed.path == "/llm/provider":
                    if not self._authorized():
                        self._send_json({"ok": False, "error": "Unauthorized"}, status=401)
                        return
                    if channel.llm_control is None:
                        self._send_json({"ok": False, "error": "LLM provider control is unavailable"}, status=503)
                        return
                    payload = self._read_json()
                    try:
                        result = channel.llm_control.select_provider(str(payload.get("provider") or ""))
                    except ValueError as exc:
                        self._send_json({"ok": False, "error": str(exc)}, status=400)
                        return
                    self._send_json({"ok": True, **result})
                    return
                if parsed.path == "/memory/redact":
                    if not self._authorized() or channel.memory_admin is None:
                        self._send_json({"ok": False, "error": "Memory API unavailable"}, status=503)
                        return
                    payload = self._read_json()
                    try:
                        channel.memory_admin.retention.redact(str(payload.get("message_id") or ""))
                    except (ValueError, FileNotFoundError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, status=400)
                        return
                    self._send_json({"ok": True})
                    return
                if parsed.path == "/memory/long-term/action":
                    if not self._authorized() or channel.memory_admin is None:
                        self._send_json({"ok": False, "error": "Memory API unavailable"}, status=503)
                        return
                    payload = self._read_json()
                    manager, memory_id, action = channel.memory_admin.personal_memory, str(payload.get("memory_id") or ""), str(payload.get("action") or "")
                    try:
                        if action == "confirm": result = manager.confirm(memory_id)
                        elif action == "correct": result = manager.supersede(memory_id, str(payload.get("content") or ""))
                        elif action == "archive": result = manager.archive(memory_id)
                        elif action == "forget": result = manager.forget(memory_id)
                        else: raise ValueError("Unsupported memory action")
                    except (ValueError, FileNotFoundError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, status=400)
                        return
                    self._send_json({"ok": True, "memory": result.to_dict()})
                    return
                self._send_json({"ok": False, "error": "Not found"}, status=404)

            def log_message(self, format, *args):
                return

            def _read_json(self) -> dict:
                length = int(self.headers.get("content-length") or "0")
                if length <= 0:
                    return {}
                raw = self.rfile.read(length).decode("utf-8")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    return {}
                return data if isinstance(data, dict) else {}

            def _authorized(self) -> bool:
                if not channel.auth_token:
                    return True
                header = self.headers.get("authorization") or ""
                token = self.headers.get("x-aniyaagent-token") or ""
                if header.lower().startswith("bearer "):
                    token = header[7:].strip()
                return token == channel.auth_token

            def _send_json(self, payload: dict, status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self._cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_empty(self, status: int) -> None:
                self.send_response(status)
                self._cors_headers()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _send_sse(self, request_id: str) -> None:
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                for event in channel.stream(request_id):
                    event_name = str(event.get("type") or "event")
                    body = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"event: {event_name}\ndata: {body}\n\n".encode("utf-8"))
                    self.wfile.flush()

            def _cors_headers(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "authorization, content-type, x-aniyaagent-token")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        return Handler
