import hashlib
import json
import os
from dataclasses import dataclass


DEFAULT_MAX_TURNS = 40
DEFAULT_REPEAT_LIMIT = 2


@dataclass
class LoopGuardState:
    turn_count: int = 0
    repeat_count: int = 0
    last_signature: str = ""


class LoopGuard:
    def __init__(self, max_turns: int | None = None, repeat_limit: int | None = None):
        self.max_turns = int(os.getenv("LOOP_GUARD_MAX_TURNS", max_turns or DEFAULT_MAX_TURNS))
        self.repeat_limit = int(
            os.getenv("LOOP_GUARD_REPEAT_LIMIT", repeat_limit or DEFAULT_REPEAT_LIMIT)
        )

    def new_state(self) -> LoopGuardState:
        return LoopGuardState()

    def begin_turn(self, state: LoopGuardState) -> str | None:
        state.turn_count += 1
        if state.turn_count > self.max_turns:
            return "stop"
        return None

    def observe_turn(self, state: LoopGuardState, messages: list, needs_more_tools: bool) -> str | None:
        if not needs_more_tools:
            self.reset(state)
            return None

        signature = self.turn_signature(messages)
        if not signature:
            return None

        if signature == state.last_signature:
            state.repeat_count += 1
        else:
            state.last_signature = signature
            state.repeat_count = 0

        if state.repeat_count == 1:
            return "nudge"
        if state.repeat_count >= self.repeat_limit:
            return "stop"
        return None

    def reset(self, state: LoopGuardState) -> None:
        state.turn_count = 0
        state.repeat_count = 0
        state.last_signature = ""

    def turn_signature(self, messages: list) -> str:
        if len(messages) < 2:
            return ""

        tail = messages[-2:]
        normalized = [self.normalize_message(message) for message in tail]
        payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def normalize_message(self, message) -> dict:
        role = message.get("role")
        content = message.get("content")

        if isinstance(content, list):
            return {"role": role, "content": [self.normalize_block(block) for block in content]}

        if isinstance(content, str):
            return {"role": role, "content": self.normalize_text(content)}

        return {"role": role, "content": self.normalize_text(str(content))}

    def normalize_block(self, block) -> dict:
        if not isinstance(block, dict):
            return {"type": type(block).__name__, "value": self.normalize_text(str(block))}

        block_type = block.get("type")
        if block_type == "tool_use":
            return {
                "type": "tool_use",
                "name": block.get("name", ""),
                "input": self.normalize_value(block.get("input")),
            }
        if block_type == "tool_result":
            return {"type": "tool_result", "content": self.normalize_value(block.get("content"))}
        if block_type == "text":
            return {"type": "text", "text": self.normalize_text(str(block.get("text", "")))}

        return {"type": block_type or "unknown", "value": self.normalize_value(block)}

    def normalize_value(self, value):
        if isinstance(value, dict):
            return {
                key: self.normalize_value(inner)
                for key, inner in sorted(value.items(), key=lambda item: str(item[0]))
            }
        if isinstance(value, list):
            return [self.normalize_value(item) for item in value]
        if value is None:
            return None
        if isinstance(value, str):
            return self.normalize_text(value)
        return value

    def normalize_text(self, text: str, limit: int = 1000) -> str:
        cleaned = " ".join(text.split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit] + f"...[len={len(cleaned)}]"
