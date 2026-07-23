from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any, Iterator

from .base import AgentResponse, ChannelMessage, ChannelSendResult
from .runtime import ChannelRuntime
from .types import ChannelKind, TrustLevel
from main.application.run_events import RunEventStore
from main.runtime.models import RunRequest


class WebChannel:
    """HTTP-independent bridge for Web requests, SSE queues and permission replies."""

    def __init__(self, channel_runtime: ChannelRuntime, channel_id: str = "web", *, auth_token: str = "", permission_timeout_seconds: int = 300, llm_control=None, memory_admin=None, application=None):
        self.channel_runtime = channel_runtime; self.channel_id = channel_id; self.kind = ChannelKind.WEB; self.trust_level = TrustLevel.HIGH
        self.auth_token = auth_token; self.permission_timeout_seconds = permission_timeout_seconds; self.llm_control = llm_control; self.memory_admin = memory_admin; self.application = application
        self.request_sessions: dict[str, str] = {}; self.conversation_requests: dict[str, str] = {}; self.permission_replies: dict[str, queue.Queue[bool]] = {}; self.permission_runs: dict[str, str] = {}
        self._lock = threading.RLock(); self._local = threading.local()
        self.run_events = RunEventStore(channel_runtime.agent_runtime.workdir)

    def submit_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or payload.get("content") or payload.get("message") or "").strip()
        if not text: return {"ok": False, "error": "Message text is required."}
        track = self.resolve_track(payload, create=True)
        if not track["can_send"]: return {"ok": False, "error": track["unavailable_reason"], "track": track}
        conversation_id = "personal" if track["mode"] == "assistant" else track["track_id"]
        request_id = uuid.uuid4().hex
        with self._lock:
            self.request_sessions[request_id] = conversation_id; self.conversation_requests[conversation_id] = request_id
        self.run_events.create(request_id, conversation_id, track["track_id"])
        metadata = {**dict(payload.get("metadata") or {}), **{key: track[key] for key in ("mode", "scope_id", "track_id", "repository_id", "work_session_id", "topic_id")}}
        attachment_ids = [str(item) for item in payload.get("attachment_ids", [])]
        if attachment_ids and self.application is not None:
            attachment_text, attachment_images = self.application.attachments.context(attachment_ids)
            if attachment_text:
                text = f"{text}\n\n<attached_context>\n{attachment_text}\n</attached_context>"
            metadata["attachment_ids"] = attachment_ids
            metadata["attachment_images"] = attachment_images
        message = ChannelMessage(self.channel_id, "local", conversation_id, text, ChannelKind.WEB, TrustLevel.HIGH, list(payload.get("files") or []), list(payload.get("images") or []) + metadata.get("attachment_images", []), metadata)
        threading.Thread(target=self._run_request, args=(request_id, message, track), daemon=True, name=f"web-request-{request_id[:8]}").start()
        return {"ok": True, "request_id": request_id, "conversation_id": conversation_id, "track": track, "stream_url": f"/stream?request_id={request_id}"}

    def resolve_track(self, payload: dict[str, Any], create: bool = False, force_new: bool = False) -> dict[str, Any]:
        mode = str(payload.get("mode") or "assistant").lower()
        if mode not in {"assistant", "coding", "qa"}: raise ValueError("Unsupported conversation mode")
        if mode == "assistant": return {"mode":"assistant","scope_id":"personal","track_id":"assistant:personal","repository_id":"","work_session_id":"","topic_id":"","can_send":True,"unavailable_reason":""}
        if self.application is None: return {"mode":mode,"scope_id":"","track_id":"","repository_id":"","work_session_id":"","topic_id":"","can_send":False,"unavailable_reason":"Application is unavailable."}
        if mode == "qa":
            topic_id = "" if force_new else str(payload.get("topic_id") or self.application.qa.active_topic())
            if not topic_id and create: topic_id = self.application.qa.new_topic()
            return {"mode":"qa","scope_id":"knowledge","track_id":f"qa:{topic_id}","repository_id":"","work_session_id":"","topic_id":topic_id,"can_send":bool(topic_id),"unavailable_reason":"Unable to create Q&A topic."}
        root = str(payload.get("repository_root") or self.application.coding.workdir); repository_id = self.application.coding.repository_id(root)
        session_id = "" if force_new else str(payload.get("work_session_id") or "")
        if not session_id and create: session_id = self.application.coding.new_work_session(root)["work_session_id"]
        return {"mode":"coding","scope_id":repository_id,"track_id":f"coding:{repository_id}:{session_id}","repository_id":repository_id,"work_session_id":session_id,"topic_id":"","repository_root":root,"can_send":bool(session_id),"unavailable_reason":"Unable to create coding work session."}

    def send(self, response: AgentResponse) -> ChannelSendResult:
        with self._lock: request_id = self.conversation_requests.get(response.conversation_id)
        if not request_id: return ChannelSendResult(False, "no active Web stream; not a durable delivery target")
        self._enqueue(request_id, {"type":"response","conversation_id":response.conversation_id,"run_id":response.run_id,"status":response.status,"content":response.text,"error":response.error,"metadata":response.metadata})
        return ChannelSendResult(True, "queued")

    def ask_user(self, block, reason: str) -> bool:
        request_id = getattr(self._local, "request_id", "")
        if not request_id: return False
        permission_id = f"perm_{uuid.uuid4().hex[:12]}"; reply_queue: queue.Queue[bool] = queue.Queue(maxsize=1)
        with self._lock:
            self.permission_replies[permission_id] = reply_queue
            self.permission_runs[permission_id] = request_id
        self._enqueue(request_id, {"type":"permission_request","request_id":permission_id,"tool":str(getattr(block,"name", "")),"reason":reason,"input":dict(getattr(block,"input",{}) or {})})
        try: return bool(reply_queue.get(timeout=self.permission_timeout_seconds))
        except queue.Empty: return False
        finally:
            with self._lock:
                self.permission_replies.pop(permission_id, None)
                self.permission_runs.pop(permission_id, None)

    def answer_permission(self, permission_id: str, allow: bool) -> bool:
        with self._lock:
            reply = self.permission_replies.get(permission_id)
            run_id = self.permission_runs.get(permission_id, "")
        if reply is None: return False
        try:
            reply.put_nowait(bool(allow))
            if run_id: self.run_events.publish(run_id, "running", {"permission_id": permission_id, "allow": bool(allow)})
            return True
        except queue.Full: return False

    def stream(self, request_id: str, after_sequence: int = 0) -> Iterator[dict]:
        last_event_id = max(0, int(after_sequence))
        if self.run_events.state(request_id) is None:
            yield {"type":"error","error":"Unknown request_id"}
            return
        while True:
            events = self.run_events.wait_for_events(request_id, last_event_id, timeout=15)
            if events:
                for event in events:
                    last_event_id = max(last_event_id, int(event.get("event_id") or 0))
                    yield event
                    if event.get("type") in {"completed", "failed", "cancelled", "timed_out"}:
                        return
                continue
            state = self.run_events.state(request_id)
            if state is None:
                yield {"type":"error","error":"Unknown request_id"}
                return
            if state["status"] in {"completed", "failed", "cancelled", "timed_out"}:
                return
            yield {"type":"ping", "time":time.time()}

    def cleanup_request(self, request_id: str) -> None:
        with self._lock:
            if request_id == "*":
                self.request_sessions.clear(); self.conversation_requests.clear(); self.permission_replies.clear(); self.permission_runs.clear(); return
            conversation = self.request_sessions.pop(request_id, "")
            if conversation and self.conversation_requests.get(conversation) == request_id: self.conversation_requests.pop(conversation, None)

    def close(self) -> None:
        self.cleanup_request("*")

    def cancel_run(self, run_id: str) -> bool:
        return self.run_events.cancel(run_id)

    def run_state(self, run_id: str) -> dict | None:
        return self.run_events.state(run_id)

    def active_runs(self, conversation_id: str = "") -> list[dict]:
        return self.run_events.active(conversation_id)

    def _run_request(self, request_id: str, message: ChannelMessage, track: dict[str, Any]) -> None:
        self._local.request_id = request_id
        self.run_events.publish(request_id, "running", {"track": track})
        try:
            request = RunRequest(request_id, message.user_id, message.channel_id, message.conversation_id, track["mode"], track["track_id"], message.text, {**message.metadata, "repository_root":track.get("repository_root", "")})
            result = self.application.run_coordinator.execute(request, emit=lambda kind, payload: self._on_runtime_event(request_id, kind, payload))
            if self.run_events.is_cancelled(request_id):
                self.run_events.finish(request_id, "cancelled", error_code="user_requested", error_message="Run cancelled", payload={"track": track})
            else:
                status = str(result.status or "failed").lower()
                if status == "completed":
                    terminal = "completed"
                elif status in {"cancelled", "timed_out"}:
                    terminal = status
                else:
                    terminal = "failed"
                self.run_events.finish(
                    request_id,
                    terminal,
                    content=result.output if terminal == "completed" else "",
                    error_code="" if terminal == "completed" else status,
                    error_message=result.error if terminal != "completed" else "",
                    metadata=result.metadata,
                    payload={"track": track},
                )
        except Exception as exc:
            self.run_events.finish(request_id, "failed", error_code=type(exc).__name__, error_message=f"{type(exc).__name__}: {exc}", payload={"track": track})
        finally:
            self._local.request_id = ""
            self.cleanup_request(request_id)

    def _on_runtime_event(self, request_id: str, event_type: str, payload: dict | None) -> None:
        mapped = {"loop.turn.started":"phase","loop.turn.completed":"phase","loop.turn.failed":"phase","llm.request.started":"llm_start","llm.request.completed":"llm_end","llm.request.failed":"llm_error","tool.call.started":"tool_start","tool.call.completed":"tool_end","tool.call.blocked":"tool_blocked","checkpoint.saved":"checkpoint"}.get(event_type,"event")
        self._enqueue(request_id, {"type":mapped,"event":event_type,"data":payload or {}})

    def _enqueue(self, request_id: str, event: dict) -> None:
        event_type = str(event.get("type") or "event")
        payload = {key: value for key, value in event.items() if key != "type"}
        self.run_events.publish(request_id, event_type, payload)
