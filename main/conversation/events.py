from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


@dataclass(frozen=True)
class ConversationEvent:
    name: str
    owner_id: str
    payload: dict
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:16]}")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class ConversationEventBus:
    """In-process event boundary; gateways decide which authenticated devices receive it."""

    def __init__(self):
        self._handlers: dict[str, list[Callable[[ConversationEvent], None]]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event_name: str, handler: Callable[[ConversationEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._handlers.setdefault(event_name, []).append(handler)
        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(event_name, [])
                if handler in handlers: handlers.remove(handler)
        return unsubscribe

    def publish(self, event: ConversationEvent) -> None:
        with self._lock:
            handlers = [*self._handlers.get(event.name, []), *self._handlers.get("*", [])]
        for handler in handlers:
            handler(event)
