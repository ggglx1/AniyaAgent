from __future__ import annotations

import time
from dataclasses import dataclass


class RunDeadlineExceeded(TimeoutError):
    pass


@dataclass(frozen=True)
class RunDeadline:
    expires_at: float

    @classmethod
    def after(cls, seconds: float) -> "RunDeadline":
        return cls(time.monotonic() + max(0.0, seconds))

    def remaining(self) -> float:
        return self.expires_at - time.monotonic()

    def require_remaining(self, component_timeout: float | None = None) -> float:
        remaining = self.remaining()
        if remaining <= 0: raise RunDeadlineExceeded("Run deadline exceeded")
        return min(remaining, component_timeout) if component_timeout else remaining
