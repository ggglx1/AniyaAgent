from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone

from main.conversation import ConversationRetentionService
from main.events import DomainEventOutbox


class SchedulerService:
    """The only owner of recurring work. SQLite leases prevent duplicate schedulers."""

    def __init__(self, runtime_module, repository, application=None):
        self.runtime = runtime_module; self.repository = repository; self.application = application; self.worker_id = f"scheduler-{os.getpid()}-{uuid.uuid4().hex[:8]}"; self._stop = threading.Event(); self._thread = None; self.outbox = DomainEventOutbox(runtime_module.WORKDIR)

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
        handled += self.consume_domain_events()
        for item in self.repository.claim_maintenance(self.worker_id):
            try:
                kind = item['kind']
                if kind == 'memory_pipeline':
                    payload = item.get('payload') or {}
                    if not payload:
                        import json
                        payload = json.loads(item.get('payload_json') or '{}')
                    self.runtime.memory_pipeline.process(payload.get('message_ids') or [], user_id='local', mode=payload.get('mode', 'assistant'), repository_id=payload.get('repository_id', ''))
                elif kind == 'proactive_event':
                    if self.application is None:
                        raise RuntimeError('proactive executor is unavailable')
                    import json
                    from main.runtime.models import RunRequest
                    payload = json.loads(item.get('payload_json') or '{}')
                    event = dict(payload.get('event') or payload)
                    run_id = str(event.get('run_id') or f"proactive_{uuid.uuid4().hex[:16]}")
                    self.application.run_coordinator.execute(RunRequest(run_id, 'local', 'scheduler', 'personal', 'assistant', 'assistant:personal', str(event.get('content') or ''), {'proactive_event': event}))
                elif kind in {'memory_maintenance', 'daily_memory', 'project_summary'}: self.runtime.memory_maintenance.tick()
                elif kind == 'retention_cleanup': ConversationRetentionService(self.runtime.conversation_memory.repository, self.runtime.personal_memory_manager).cleanup_expired_operational_artifacts()
                else:
                    raise ValueError(f'unsupported_kind:{kind}')
                self.repository.complete_maintenance(item['id'], item['claim_token']); handled += 1
            except Exception as exc:
                self.repository.complete_maintenance(item['id'], item['claim_token'], f'{type(exc).__name__}: {exc}')
        self.repository.expire_track_messages(datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
        self.runtime.reminder_dispatcher.tick()
        self.runtime.routine_dispatcher.tick()
        self.reconcile_outbox()
        return {'handled': handled}

    def consume_domain_events(self) -> int:
        handled = 0
        for event in self.outbox.claim(self.worker_id):
            try:
                payload = event["payload"]
                if event["event_type"] in {"conversation.completed", "assistant.conversation.completed", "deliberative.completed"}:
                    self.runtime.memory_pipeline.process(payload.get("factual_message_ids") or [], user_id="local", mode=payload.get("mode", "assistant"), repository_id=payload.get("repository_id", ""))
                elif event["event_type"] == "qa.conversation.completed":
                    pass  # QA facts have retention/audit value but never enter personal memory.
                elif event["event_type"] == "assistant.action.completed":
                    pass  # Domain action has already committed; this is audit-only.
                elif event["event_type"] == "coding.completed":
                    self.runtime.memory_pipeline.process(payload.get("factual_message_ids") or [], user_id="local", mode="coding", repository_id=payload.get("repository_id", ""))
                    self.runtime.memory_maintenance.tick()
                elif event["event_type"] == "action.executed":
                    pass  # The domain service is already canonical; this is an auditable extension point.
                elif event["event_type"] == "daily_memory.due":
                    self.runtime.memory_maintenance.tick()
                elif event["event_type"] == "project_summary.requested":
                    # Project summaries are a dedicated event boundary; the current
                    # implementation rebuilds only the coding-memory projection.
                    self.runtime.memory_maintenance.tick()
                elif event["event_type"] == "notification.requested":
                    self.runtime.reminder_dispatcher.tick()
                elif event["event_type"] == "run.failed":
                    pass  # Kept as a durable audit/statistics event, never retried as memory work.
                else:
                    raise ValueError(f"unsupported_event:{event['event_type']}")
                self.outbox.complete(event["event_id"], event["claim_token"]); handled += 1
            except Exception as exc:
                self.outbox.fail(event["event_id"], event["claim_token"], f"{type(exc).__name__}: {exc}", retryable=not isinstance(exc, ValueError))
        return handled

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

    def health(self) -> dict:
        now = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        with self.repository.connect() as connection:
            lease = connection.execute("SELECT * FROM scheduler_lease WHERE lease_name='primary'").fetchone()
            pending = connection.execute("SELECT COUNT(*) FROM maintenance_requests WHERE state IN ('pending','claimed')").fetchone()[0]
        outbox = self.runtime.reminder_dispatcher.delivery_outbox
        return {"online": bool(lease and lease['expires_at'] > now), "worker_id": lease['worker_id'] if lease else "", "heartbeat": lease['updated_at'] if lease else "", "pending_jobs": pending, "unknown_deliveries": len(outbox.unknown_deliveries()), "domain_events": self.outbox.stats()}
