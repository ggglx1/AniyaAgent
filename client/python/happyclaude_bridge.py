#!/usr/bin/env python3

import contextlib
import io
import json
import queue
import sys
import threading
import traceback
from pathlib import Path


CLIENT_DIR = Path(__file__).resolve().parents[1]
ROOT = CLIENT_DIR.parent
MAIN_DIR = ROOT / "Main"
sys.path.insert(0, str(MAIN_DIR))

import MainLoop  # noqa: E402


history = []
write_lock = threading.Lock()
permission_replies: dict[str, "queue.Queue[bool]"] = {}
permission_counter = 0


def emit(event: dict) -> None:
    with write_lock:
        print(json.dumps(event, ensure_ascii=False), flush=True)


class Capture(io.TextIOBase):
    def __init__(self, role: str = "log"):
        self.role = role
        self._buffer = ""

    def writable(self):
        return True

    def write(self, text):
        if not text:
            return 0
        self._buffer += str(text)
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                emit({"type": "output", "role": self.role, "content": line})
        return len(text)

    def flush(self):
        if self._buffer.strip():
            emit({"type": "output", "role": self.role, "content": self._buffer.strip()})
        self._buffer = ""


def extract_text(message_content) -> str:
    if not isinstance(message_content, list):
        return str(message_content)

    parts = []
    for block in message_content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def patch_permissions() -> None:
    def web_ask_user(block, reason: str) -> bool:
        global permission_counter
        permission_counter += 1
        request_id = f"perm_{permission_counter}"
        reply_queue: "queue.Queue[bool]" = queue.Queue(maxsize=1)
        permission_replies[request_id] = reply_queue
        emit(
            {
                "type": "permission_request",
                "request_id": request_id,
                "tool": getattr(block, "name", ""),
                "reason": reason,
                "input": dict(getattr(block, "input", {}) or {}),
            }
        )
        try:
            return bool(reply_queue.get(timeout=300))
        except queue.Empty:
            return False
        finally:
            permission_replies.pop(request_id, None)

    MainLoop.permissions.ask_user = web_ask_user


def run_agent(content: str) -> None:
    emit({"type": "status", "status": "busy"})
    history.append({"role": "user", "content": content})
    capture = Capture("log")
    try:
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            MainLoop.hooks.trigger("UserPromptSubmit", content)
            MainLoop.agent_loop(history)
        capture.flush()
        final = extract_text(history[-1].get("content", "")) if history else ""
        if final:
            emit({"type": "output", "role": "assistant", "content": final})
    except Exception:
        capture.flush()
        emit({"type": "output", "role": "error", "content": traceback.format_exc()})
    finally:
        emit({"type": "status", "status": "ready"})


def handle_message(message: dict) -> None:
    msg_type = message.get("type")
    if msg_type == "send":
        content = str(message.get("content") or "").strip()
        if content:
            threading.Thread(target=run_agent, args=(content,), daemon=True).start()
        return

    if msg_type == "permission_response":
        request_id = str(message.get("request_id") or "")
        queue_for_reply = permission_replies.get(request_id)
        if queue_for_reply is not None:
            queue_for_reply.put(bool(message.get("allow")))
        return

    emit({"type": "output", "role": "error", "content": f"Unknown bridge message: {msg_type}"})


def main() -> None:
    patch_permissions()
    emit({"type": "ready", "model": MainLoop.MODEL})
    emit({"type": "status", "status": "ready"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle_message(json.loads(line))
        except Exception:
            emit({"type": "output", "role": "error", "content": traceback.format_exc()})


if __name__ == "__main__":
    main()
