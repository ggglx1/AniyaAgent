from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


class AgendaQueryService:
    """Read-only date view over the personal-state database."""

    OPEN_TASK_STATUSES = {"inbox", "planned", "waiting", "in_progress", "deferred"}

    def __init__(self, state, profile):
        self.state = state
        self.profile = profile

    def view(self, selected_date: str = "", timezone_name: str = "") -> dict:
        zone_name = timezone_name or (self.profile.get().get("timezone") or "Asia/Shanghai")
        zone = ZoneInfo(zone_name)
        target = date.fromisoformat(selected_date) if selected_date else datetime.now(zone).date()
        start = datetime.combine(target, time.min, tzinfo=zone).astimezone(timezone.utc)
        end = start + timedelta(days=1)
        today = datetime.now(zone).date()
        tasks = [item.to_dict() for item in self.state.list_tasks(limit=500)]
        reminders = [item.to_dict() for item in self.state.list_reminders(limit=500)]
        activity = self.state.repository.activity(self.state.user_id, limit=500)
        receipts = self.state.repository.list_receipts(self.state.user_id, self.iso(start), self.iso(end), limit=200)

        focus, overdue, unscheduled = [], [], []
        completed_ids = self.completed_ids(activity, target, zone)
        for task in tasks:
            due = self.parse(task.get("due_at", ""))
            if task["status"] == "done" and task["id"] in completed_ids:
                continue
            if task["status"] not in self.OPEN_TASK_STATUSES:
                continue
            if due and due.astimezone(zone).date() == target:
                focus.append(task)
            elif target == today and due and due.astimezone(zone).date() < target:
                overdue.append(task)
            elif target == today and not due:
                unscheduled.append(task)

        scheduled, delivered = [], []
        for reminder in reminders:
            effective = self.parse(reminder.get("snoozed_until") or reminder.get("scheduled_at", ""))
            if reminder.get("status") in {"scheduled", "snoozed"} and effective and effective.astimezone(zone).date() == target:
                scheduled.append(reminder)
            delivered_at = self.parse(reminder.get("last_delivered_at", ""))
            if delivered_at and delivered_at.astimezone(zone).date() == target:
                delivered.append(reminder)

        completed = [item for item in tasks if item["id"] in completed_ids]
        pending_receipts = [item for item in receipts if item["status"] in {"waiting_input", "preview_required", "confirmation_required"}]
        return {
            "date": target.isoformat(), "timezone": zone_name, "is_today": target == today,
            "summary": {"focus_count": len(focus), "reminder_count": len(scheduled), "overdue_count": len(overdue), "pending_action_count": len(pending_receipts)},
            "focus_tasks": self.sort_tasks(focus), "scheduled_reminders": self.sort_reminders(scheduled),
            "delivered_reminders": self.sort_reminders(delivered), "completed_items": completed,
            "overdue_tasks": self.sort_tasks(overdue), "unscheduled_tasks": self.sort_tasks(unscheduled),
            "recent_receipts": receipts, "next_date_with_items": "", "previous_date_with_items": "",
        }

    def completed_ids(self, activity: list[dict], target: date, zone: ZoneInfo) -> set[str]:
        return {
            item["entity_id"] for item in activity
            if item.get("entity_type") in {"task", "reminder"}
            and item.get("operation") in {"updated", "completed"}
            and self.parse(item.get("created_at", ""))
            and self.parse(item["created_at"]).astimezone(zone).date() == target
            and str((item.get("after") or {}).get("status") or "") in {"done", "completed"}
        }

    def dates(self, from_date: str, to_date: str) -> list[dict]:
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
        if end < start or (end - start).days > 366:
            raise ValueError("Date range must be from 0 to 366 days")
        result = []
        current = start
        while current <= end:
            agenda = self.view(current.isoformat())
            if any(agenda[key] for key in ("focus_tasks", "scheduled_reminders", "completed_items", "recent_receipts")):
                result.append({"date": current.isoformat(), "summary": agenda["summary"]})
            current += timedelta(days=1)
        return result

    def sort_tasks(self, items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda item: (0 if item.get("status") == "in_progress" else 1, item.get("priority", 3), item.get("due_at") or "9999"))

    def sort_reminders(self, items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda item: item.get("snoozed_until") or item.get("scheduled_at") or "")

    def parse(self, value: str) -> datetime | None:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

    def iso(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
