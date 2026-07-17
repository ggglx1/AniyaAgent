from __future__ import annotations

import threading


class Lifecycle:
    """Idempotent process lifecycle; importing services never starts worker threads."""

    def __init__(self):
        self._started = False
        self._lock = threading.RLock()

    def start_once(self, starter) -> bool:
        with self._lock:
            if self._started:
                return False
            starter()
            self._started = True
            return True

    def stop(self, stopper) -> None:
        with self._lock:
            if not self._started:
                return
            stopper()
            self._started = False
