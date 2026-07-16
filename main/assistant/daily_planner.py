import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


class DailyPlanner:
    def __init__(self, workdir: Path, state, profile):
        self.workdir = workdir.resolve()
        self.state = state
        self.profile = profile
        self.workspace_dir = self.workdir / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def today_overview(self, now: datetime | None = None) -> dict:
        current = self.utc_now(now)
        profile = self.profile.get()
        timezone_name = profile.get("timezone") or "Asia/Shanghai"
        zone = ZoneInfo(timezone_name)
        local_now = current.astimezone(zone)
        today = local_now.date()

        tasks = self.state.list_tasks(limit=500)
        reminders = self.state.list_reminders(limit=500)
        projects = self.state.list_projects(statuses=["active", "paused"], limit=100)
        open_statuses = {"inbox", "planned", "waiting", "in_progress", "deferred"}

        overdue_tasks = []
        due_today_tasks = []
        completed_today = []
        stale_tasks = []
        unscheduled_tasks = []
        for task in tasks:
            if task.status == "done" and self.on_local_date(task.updated_at, today, zone):
                completed_today.append(task.to_dict())
                continue
            if task.status not in open_statuses:
                continue
            due = self.parse_datetime(task.due_at)
            if due and due < current:
                overdue_tasks.append(task.to_dict())
            elif due and due.astimezone(zone).date() == today:
                due_today_tasks.append(task.to_dict())
            elif not due:
                unscheduled_tasks.append(task.to_dict())
            updated = self.parse_datetime(task.updated_at)
            if updated and updated < current - timedelta(days=7):
                stale_tasks.append(task.to_dict())

        due_reminders = []
        upcoming_reminders = []
        delivered_today = []
        for reminder in reminders:
            effective = self.parse_datetime(reminder.snoozed_until or reminder.scheduled_at)
            if reminder.status in {"scheduled", "snoozed"} and effective:
                if effective <= current:
                    due_reminders.append(reminder.to_dict())
                elif effective.astimezone(zone).date() == today:
                    upcoming_reminders.append(reminder.to_dict())
            if reminder.last_delivered_at and self.on_local_date(reminder.last_delivered_at, today, zone):
                delivered_today.append(reminder.to_dict())

        overview = {
            "date": today.isoformat(),
            "timezone": timezone_name,
            "generated_at": self.iso(current),
            "overdue_tasks": self.sort_tasks(overdue_tasks),
            "due_today_tasks": self.sort_tasks(due_today_tasks),
            "completed_today": completed_today,
            "stale_tasks": self.sort_tasks(stale_tasks),
            "unscheduled_tasks": self.sort_tasks(unscheduled_tasks),
            "due_reminders": self.sort_reminders(due_reminders),
            "upcoming_reminders": self.sort_reminders(upcoming_reminders),
            "delivered_today": delivered_today,
            "active_projects": [project.to_dict() for project in projects],
        }
        overview["counts"] = {
            key: len(value)
            for key, value in overview.items()
            if isinstance(value, list)
        }
        self.write_today(overview)
        return overview

    def morning_plan(self, now: datetime | None = None) -> dict:
        overview = self.today_overview(now)
        profile = self.profile.get()
        preferences = profile.get("planning_preferences") or {}
        focus_limit = preferences.get("focus_limit", 3)
        try:
            focus_limit = max(1, min(int(focus_limit), 5))
        except (TypeError, ValueError):
            focus_limit = 3

        candidates = []
        seen = set()
        for group in ("overdue_tasks", "due_today_tasks", "unscheduled_tasks"):
            for task in overview[group]:
                if task["id"] not in seen:
                    candidates.append(task)
                    seen.add(task["id"])
        focus = candidates[:focus_limit]
        risks = []
        if overview["overdue_tasks"]:
            risks.append(f"{len(overview['overdue_tasks'])} task(s) are overdue")
        if overview["stale_tasks"]:
            risks.append(f"{len(overview['stale_tasks'])} task(s) have not moved for at least 7 days")
        for project in overview["active_projects"]:
            if project["status"] == "active" and not project["next_action"]:
                risks.append(f"Project '{project['name']}' has no next action")

        plan = {
            "date": overview["date"],
            "focus_tasks": focus,
            "reminders": overview["due_reminders"] + overview["upcoming_reminders"],
            "risks": risks,
            "suggested_first_action": self.first_action(focus),
            "note": "This plan is a suggestion and does not silently change task state.",
        }
        self.write_json_view("MORNING_PLAN.md", "Morning Plan", plan)
        return plan

    def evening_review(self, now: datetime | None = None) -> dict:
        overview = self.today_overview(now)
        unfinished = overview["overdue_tasks"] + overview["due_today_tasks"]
        review = {
            "date": overview["date"],
            "completed_tasks": overview["completed_today"],
            "unfinished_due_tasks": unfinished,
            "delivered_reminders": overview["delivered_today"],
            "stale_tasks": overview["stale_tasks"],
            "active_projects": overview["active_projects"],
            "reflection_questions": self.reflection_questions(overview, unfinished),
            "note": "Review output is observational; it does not create memories or commitments automatically.",
        }
        self.write_json_view("EVENING_REVIEW.md", "Evening Review", review)
        return review

    def weekly_review(self, now: datetime | None = None) -> dict:
        current = self.utc_now(now)
        week_start = current - timedelta(days=7)
        tasks = self.state.list_tasks(limit=500)
        projects = self.state.list_projects(statuses=["active", "paused"], limit=100)
        completed = [
            task.to_dict() for task in tasks
            if task.status == "done" and (self.parse_datetime(task.updated_at) or current) >= week_start
        ]
        open_tasks = [
            task.to_dict() for task in tasks
            if task.status in {"inbox", "planned", "waiting", "in_progress", "deferred"}
        ]
        stale = [
            task for task in open_tasks
            if (self.parse_datetime(task.get("updated_at", "")) or current) < week_start
        ]
        review = {
            "period_start": self.iso(week_start),
            "period_end": self.iso(current),
            "completed_tasks": completed,
            "open_tasks": self.sort_tasks(open_tasks),
            "stale_tasks": self.sort_tasks(stale),
            "active_projects": [project.to_dict() for project in projects],
            "reflection_questions": [
                "Which result mattered most this week?",
                "Which open commitment should be prioritized, delegated, deferred, or cancelled?",
                "Does every active project have a concrete next action?",
            ],
            "note": "Weekly review is observational and does not silently modify personal state.",
        }
        self.write_json_view("WEEKLY_REVIEW.md", "Weekly Review", review)
        return review

    def reflection_questions(self, overview: dict, unfinished: list[dict]) -> list[str]:
        questions = ["What was the most useful progress today?"]
        if unfinished:
            questions.append("Which unfinished item should be rescheduled, delegated, or cancelled?")
        if overview["stale_tasks"]:
            questions.append("Do any stale tasks no longer matter?")
        if overview["active_projects"]:
            questions.append("Does each active project still have the right next action?")
        return questions

    def first_action(self, focus: list[dict]) -> str:
        if not focus:
            return "No urgent task is recorded; choose one meaningful next action."
        task = focus[0]
        return task.get("next_action") or f"Start: {task['title']}"

    def sort_tasks(self, tasks: list[dict]) -> list[dict]:
        return sorted(
            tasks,
            key=lambda item: (
                0 if item.get("status") == "in_progress" else 1,
                item.get("priority", 3),
                item.get("due_at") or "9999",
                item.get("updated_at") or "",
            ),
        )

    def sort_reminders(self, reminders: list[dict]) -> list[dict]:
        return sorted(reminders, key=lambda item: item.get("snoozed_until") or item.get("scheduled_at") or "")

    def write_today(self, overview: dict) -> None:
        lines = [
            f"# Today - {overview['date']}", "",
            f"Timezone: {overview['timezone']}", "",
            "## Counts", "",
        ]
        for key, value in overview["counts"].items():
            lines.append(f"- {key}: {value}")
        lines.extend(["", "## Priority Tasks", ""])
        priority = overview["overdue_tasks"] + overview["due_today_tasks"]
        lines.extend(
            f"- [{task['status']}] {task['title']} <!-- {task['id']} -->"
            for task in priority
        )
        if not priority:
            lines.append("(none)")
        self.atomic_write(self.workspace_dir / "TODAY.md", "\n".join(lines).rstrip() + "\n")

    def write_json_view(self, filename: str, title: str, value: dict) -> None:
        content = f"# {title}\n\n```json\n{json.dumps(value, ensure_ascii=False, indent=2)}\n```\n"
        self.atomic_write(self.workspace_dir / filename, content)

    def atomic_write(self, path: Path, content: str) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)

    def on_local_date(self, value: str, target, zone: ZoneInfo) -> bool:
        parsed = self.parse_datetime(value)
        return bool(parsed and parsed.astimezone(zone).date() == target)

    def parse_datetime(self, value: str) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def utc_now(self, value: datetime | None) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def iso(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
