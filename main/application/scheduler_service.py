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
        if not self.repository.acquire_scheduler_lease(self.worker_id):
            raise RuntimeError("Another Scheduler instance owns the active lease.")
        self._stop.clear(); self._thread = threading.Thread(target=self.run, daemon=True, name="aniyaagent-scheduler"); self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.repository.release_scheduler_lease(self.worker_id)

    def run(self) -> None:
        while not self._stop.wait(30): self.tick()

    def tick(self) -> dict:
        if not self.repository.acquire_scheduler_lease(self.worker_id): return {'handled': 0, 'standby': True}
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
        self.runtime.reminder_dispatcher.tick()
        self.runtime.routine_dispatcher.tick()
        self.reconcile_outbox()
        return {'handled': handled}

    def reconcile_outbox(self) -> None:
        dispatcher = self.runtime.reminder_dispatcher
        for item in dispatcher.delivery_outbox.unreconciled_deliveries():
            try:
                reminder = dispatcher.state.require_reminder(item['reminder_id'])
                if reminder.status not in {'delivered', 'completed'}:
                    dispatcher.state.update_reminder(reminder.id, {'status': 'delivered', 'last_delivered_at': item['delivered_at'], 'delivery_result': 'reconciled from outbox'}, source='outbox_reconciliation')
                dispatcher.delivery_outbox.mark_business_reconciled(item['id'])
            except Exception:
                # Leave it unreconciled for a later scheduler pass rather than guessing.
                continue
