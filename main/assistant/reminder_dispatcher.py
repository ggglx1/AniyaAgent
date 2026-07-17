import json
import threading
import uuid
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from main.channel.base import AgentResponse
from main.personal.scheduling import CronSchedule
from main.notifications import NotificationOutbox

from .proactive_engine import ProactiveEngine


class ReminderDispatcher:
    def __init__(self, workdir: Path, state, profile, channel_registry, interval_seconds: int = 15):
        self.workdir = workdir.resolve()
        self.state = state
        self.profile = profile
        self.channel_registry = channel_registry
        self.interval_seconds = max(1, interval_seconds)
        self.outbox = self.workdir / ".personal" / "notification_outbox.jsonl"
        self.delivery_outbox = NotificationOutbox(self.workdir)
        self.worker_id = f"reminder-dispatcher-{uuid.uuid4().hex[:8]}"
        self.engine = ProactiveEngine()
        self.started = False
        self.stop_event = threading.Event()

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        threading.Thread(target=self.run, daemon=True, name="personal-reminder-dispatcher").start()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                self.tick()
            except Exception as exc:
                self.append_outbox({"event": "dispatcher.error", "error": f"{type(exc).__name__}: {exc}"})

    def tick(self, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        profile = self.profile.get()
        local_now = current.astimezone(ZoneInfo(profile.get("timezone") or "Asia/Shanghai"))
        quiet_start, quiet_end = self.quiet_hours(profile.get("quiet_hours") or {})
        delivered = 0
        delivered += self.flush_outbox(current)
        for reminder in self.state.due_reminders(self.iso(current)):
            decision = self.engine.decide(
                local_now,
                quiet_start=quiet_start,
                quiet_end=quiet_end,
                due_reminders=[reminder.to_dict()],
                proactive_paused=bool(profile.get("proactive_paused")),
            )
            if decision.action == "deliver_reminder" and self.deliver(reminder, current):
                delivered += 1
        return delivered

    def deliver(self, reminder, delivered_at: datetime) -> bool:
        occurrence = reminder.snoozed_until or reminder.scheduled_at
        self.delivery_outbox.enqueue(
            reminder.id, reminder.target_channel, reminder.user_id,
            {"text": f"提醒：{reminder.content}", "recurrence": reminder.recurrence, "scheduled_at": occurrence}, occurrence,
        )
        return self.flush_outbox(delivered_at) > 0

    def flush_outbox(self, delivered_at: datetime) -> int:
        delivered = 0
        for item in self.delivery_outbox.claim(self.worker_id):
            token = str(item.get("claim_token") or "")
            if not self.delivery_outbox.begin_sending(item["id"], token):
                continue
            payload = json.loads(item["payload_json"])
            response = AgentResponse(channel_id=item["channel_id"], conversation_id=item["recipient_id"], run_id=f"reminder_{uuid.uuid4().hex[:12]}", status="notification", text=str(payload["text"]), metadata={"reminder_id": item["reminder_id"], "outbox_id": item["id"]})
            result = self.channel_registry.send(response)
            self.append_outbox({"event": "reminder.delivery", "reminder_id": item["reminder_id"], "channel": item["channel_id"], "ok": result.ok, "message": result.message, "created_at": self.iso(delivered_at), "outbox_id": item["id"]})
            if not result.ok:
                self.delivery_outbox.complete(item["id"], False, result.message, token)
                continue
            if not self.delivery_outbox.complete(item["id"], True, claim_token=token):
                continue
            reminder = self.state.require_reminder(item["reminder_id"])
            changes = {"last_delivered_at": self.iso(delivered_at), "snoozed_until": "", "delivery_result": result.message}
            if reminder.recurrence:
                changes.update({"status": "scheduled", "scheduled_at": CronSchedule.next_after(reminder.recurrence, delivered_at, reminder.timezone)})
            else:
                changes["status"] = "delivered"
            self.state.update_reminder(reminder.id, changes, source="dispatcher")
            self.delivery_outbox.mark_business_reconciled(item["id"])
            delivered += 1
        return delivered

    def quiet_hours(self, value: dict) -> tuple[clock_time | None, clock_time | None]:
        try:
            return (
                clock_time.fromisoformat(str(value.get("start") or "")),
                clock_time.fromisoformat(str(value.get("end") or "")),
            )
        except ValueError:
            return None, None

    def append_outbox(self, event: dict) -> None:
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        with self.outbox.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def iso(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
