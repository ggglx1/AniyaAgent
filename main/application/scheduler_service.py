from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone

from main.conversation import ConversationRetentionService


class SchedulerService:
    """The only owner of recurring work. SQLite leases prevent duplicate schedulers."""

    def __init__(self, runtime_module, repository):
        self.runtime = runtime_module; self.repository = repository; self.worker_id = f"scheduler-{os.getpid()}-{uuid.uuid4().hex[:8]}"; self._stop = threading.Event(); self._thread = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive(): return
        self._stop.clear(); self._thread = threading.Thread(target=self.run, daemon=True, name="aniyaagent-scheduler"); self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for item in (self.runtime.reminder_dispatcher, self.runtime.routine_dispatcher, self.runtime.memory_maintenance): item.stop()

    def run(self) -> None:
        self.runtime.start_background_services()
        while not self._stop.wait(30): self.tick()

    def tick(self) -> dict:
        handled = 0
        for item in self.repository.claim_maintenance(self.worker_id):
            try:
                kind = item['kind']
                if kind in {'memory_maintenance', 'daily_memory', 'project_summary'}: self.runtime.memory_maintenance.tick()
                elif kind == 'retention_cleanup': ConversationRetentionService(self.runtime.conversation_memory.repository, self.runtime.personal_memory_manager).cleanup_expired_operational_artifacts()
                self.repository.complete_maintenance(item['id'], item['claim_token']); handled += 1
            except Exception as exc:
                self.repository.complete_maintenance(item['id'], item['claim_token'], f'{type(exc).__name__}: {exc}')
        self.repository.expire_track_messages(datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
        return {'handled': handled}
