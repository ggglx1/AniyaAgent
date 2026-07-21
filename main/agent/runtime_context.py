import threading
from dataclasses import dataclass
from typing import Callable
from .deadline import RunDeadline


_local = threading.local()


@dataclass
class RuntimeContextState:
    run_id: str
    session_id: str
    audit: object
    conversations: object
    event_callback: Callable[[str, dict], None] | None = None
    turn_index: int = 0
    deadline: RunDeadline | None = None


def bind_runtime(
    run_id: str,
    session_id: str,
    audit,
    conversations,
    event_callback: Callable[[str, dict], None] | None = None,
    deadline: RunDeadline | None = None,
) -> None:
    _local.state = RuntimeContextState(
        run_id=run_id,
        session_id=session_id,
        audit=audit,
        conversations=conversations,
        event_callback=event_callback,
        deadline=deadline,
    )


def clear_runtime() -> None:
    _local.state = None


def current_state() -> RuntimeContextState | None:
    return getattr(_local, "state", None)


def remaining_seconds(component_timeout: float | None = None) -> float | None:
    state = current_state()
    return state.deadline.require_remaining(component_timeout) if state and state.deadline else None


def ensure_deadline(component_timeout: float | None = None) -> None:
    remaining_seconds(component_timeout)


def event(event_type: str, payload: dict | None = None) -> None:
    state = current_state()
    if state is None:
        return
    data = payload or {}
    state.audit.write(state.run_id, event_type, data)
    if state.event_callback is not None:
        try:
            state.event_callback(event_type, data)
        except Exception:
            pass


def checkpoint(label: str, messages: list) -> None:
    state = current_state()
    if state is None:
        return
    checkpoint_id = f"{state.run_id}_{safe_label(label)}"
    path = state.conversations.checkpoint(state.session_id, checkpoint_id, messages)
    event("checkpoint.saved", {"label": label, "path": str(path), "messages": len(messages)})


def begin_turn(messages: list) -> int:
    state = current_state()
    if state is None:
        return 0
    state.turn_index += 1
    event(
        "loop.turn.started",
        {
            "turn": state.turn_index,
            "messages": len(messages),
            "estimated_chars": len(str(messages)),
        },
    )
    checkpoint(f"turn_{state.turn_index:04d}_before", messages)
    return state.turn_index


def end_turn(messages: list, needs_more_tools: bool, used_tools: set[str]) -> None:
    state = current_state()
    if state is None:
        return
    event(
        "loop.turn.completed",
        {
            "turn": state.turn_index,
            "messages": len(messages),
            "needs_more_tools": needs_more_tools,
            "used_tools": sorted(used_tools),
            "estimated_chars": len(str(messages)),
        },
    )
    checkpoint(f"turn_{state.turn_index:04d}_after", messages)


def tool_payload(block) -> dict:
    return {
        "id": str(getattr(block, "id", "")),
        "name": str(getattr(block, "name", "")),
        "input": getattr(block, "input", {}) or {},
    }


def preview(value, limit: int = 1000) -> dict:
    text = str(value)
    return {
        "length": len(text),
        "preview": text[:limit],
        "truncated": len(text) > limit,
    }


def safe_label(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value))
