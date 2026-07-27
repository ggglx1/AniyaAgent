from __future__ import annotations

import threading
import time


class RunCancelledError(RuntimeError):
    pass


class RunTimedOutError(TimeoutError):
    pass


class CancellationToken:
    """Cooperative cancellation shared by routing, model calls and tools."""

    def __init__(self, deadline_at: float):
        self.deadline_at = deadline_at
        self._cancelled = threading.Event()
        self.reason = ""

    def cancel(self, reason: str = "user_requested") -> None:
        self.reason = reason
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_at - time.monotonic())

    def check(self) -> None:
        if self.is_cancelled():
            raise RunCancelledError(self.reason or "Run cancelled")
        if self.remaining_seconds() <= 0:
            raise RunTimedOutError("Run deadline exceeded")
