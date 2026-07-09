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
ROOT = CLIENT_DIR.parents[1]
sys.path.insert(0, str(ROOT))

from main.agent import main_loop as MainLoop  # noqa: E402
from main.channel import ChannelMessage, TrustLevel  # noqa: E402
from main.channel.local import CallbackChannel  # noqa: E402
from main.channel.types import ChannelKind  # noqa: E402

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


def patch_web_channel() -> None:
    def send_to_web(response) -> None:
        if response.text:
            emit({"type": "output", "role": "assistant", "content": response.text})
        elif response.error:
            emit({"type": "output", "role": "error", "content": response.error})

    MainLoop.channel_registry.register(
        CallbackChannel(
            channel_id="web",
            kind=ChannelKind.WEB,
            trust_level=TrustLevel.HIGH,
            sender=send_to_web,
        )
    )


def run_agent(content: str) -> None:
    emit({"type": "status", "status": "busy"})
    capture = Capture("log")
    try:
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            response = MainLoop.handle_channel_message(
                ChannelMessage(
                    channel_id="web",
                    user_id="web",
                    conversation_id="web",
                    text=content,
                    kind=ChannelKind.WEB,
                    trust_level=TrustLevel.HIGH,
                ),
                deliver=False,
            )
        capture.flush()
        if response.text:
            emit({"type": "output", "role": "assistant", "content": response.text})
        elif response.error:
            emit({"type": "output", "role": "error", "content": response.error})
    except Exception:
        capture.flush()
        emit({"type": "output", "role": "error", "content": traceback.format_exc()})
    finally:
        emit({"type": "status", "status": "ready"})


def handle_message(message: dict) -> None:
    msg_type = message.get("type")
    if msg_type == "channels_list":
        emit(
            {
                "type": "channels",
                "channels": MainLoop.channel_registry.list_channels(),
            }
        )
        return

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
    patch_web_channel()
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
