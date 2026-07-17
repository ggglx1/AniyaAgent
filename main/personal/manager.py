from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    PersonalProject,
    PersonalReminder,
    PersonalTask,
    PersonalTaskStatus,
    ProjectStatus,
    ReminderStatus,
)
from .repository import PersonalStateRepository
from .scheduling import CronSchedule
from main.conversation.repository import ConversationMemoryRepository


class PersonalStateManager:
    task_fields = {
        "title", "description", "status", "priority", "project_id", "due_at",
        "next_action", "blockers", "completion_note",
    }
    reminder_fields = {
        "content", "scheduled_at", "timezone", "recurrence", "target_channel", "status",
        "task_id", "project_id", "person_ref", "last_delivered_at", "snoozed_until",
        "delivery_result",
    }
    project_fields = {
        "name", "goal", "status", "next_action", "blockers", "key_decisions",
        "review_cadence", "last_reviewed_at",
    }

    def __init__(
        self,
        workdir: Path,
        user_id: str = "local",
        repository: PersonalStateRepository | None = None,
    ):
        self.workdir = workdir.resolve()
        self.user_id = user_id
        self.repository = repository or PersonalStateRepository(self.workdir)
        self.workspace_dir = self.workdir / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.conversation_memory = ConversationMemoryRepository(self.workdir)

    def create_task(
        self,
        title: str,
        description: str = "",
        status: str = PersonalTaskStatus.INBOX.value,
        priority: int = 3,
        project_id: str = "",
        due_at: str = "",
        next_action: str = "",
        blockers: list[str] | None = None,
        source_conversation: str = "",
    ) -> PersonalTask:
        self.require_text(title, "Task title")
        self.validate_status(status, PersonalTaskStatus)
        self.validate_priority(priority)
        due_at = self.normalize_datetime(due_at, "due_at")
        if project_id:
            self.require_project(project_id)
        now = self.now_iso()
        task = PersonalTask(
            id=self.new_id("ptask"), user_id=self.user_id, title=title.strip(),
            description=description.strip(), status=status, priority=priority,
            project_id=project_id, due_at=due_at, next_action=next_action.strip(),
            blockers=blockers or [], source_conversation=source_conversation,
            created_at=now, updated_at=now,
        )
        self.repository.insert("personal_tasks", task, self.activity("task", task.id, "created", None, task))
        self.sync_workspace()
        self.mark_daily_memory_dirty()
        return task

    def update_task(self, task_id: str, changes: dict, source: str = "user") -> PersonalTask:
        before = self.require_task(task_id)
        clean = self.validate_changes(changes, self.task_fields)
        if "status" in clean:
            self.validate_status(clean["status"], PersonalTaskStatus)
        if "priority" in clean:
            self.validate_priority(clean["priority"])
        if "due_at" in clean:
            clean["due_at"] = self.normalize_datetime(clean["due_at"], "due_at")
        if clean.get("project_id"):
            self.require_project(clean["project_id"])
        if "title" in clean:
            self.require_text(clean["title"], "Task title")
            clean["title"] = clean["title"].strip()
        after = replace(before, **clean, updated_at=self.now_iso())
        self.repository.update(
            "personal_tasks", before, after,
            self.activity("task", task_id, "updated", before, after, source),
        )
        self.sync_workspace()
        self.mark_daily_memory_dirty()
        return after

    def complete_task(self, task_id: str, note: str = "") -> PersonalTask:
        return self.update_task(
            task_id,
            {"status": PersonalTaskStatus.DONE.value, "completion_note": note},
        )

    def list_tasks(self, statuses: list[str] | None = None, limit: int = 100) -> list[PersonalTask]:
        if statuses:
            for status in statuses:
                self.validate_status(status, PersonalTaskStatus)
        return self.repository.list("personal_tasks", PersonalTask, self.user_id, statuses, limit)

    def create_reminder(
        self,
        content: str,
        scheduled_at: str,
        timezone_name: str = "Asia/Shanghai",
        recurrence: str = "",
        target_channel: str = "weixin",
        task_id: str = "",
        project_id: str = "",
        person_ref: str = "",
    ) -> PersonalReminder:
        self.require_text(content, "Reminder content")
        scheduled_at = self.normalize_datetime(scheduled_at, "scheduled_at", required=True)
        if recurrence:
            CronSchedule.validate(recurrence)
        if task_id:
            self.require_task(task_id)
        if project_id:
            self.require_project(project_id)
        now = self.now_iso()
        reminder = PersonalReminder(
            id=self.new_id("rem"), user_id=self.user_id, content=content.strip(),
            scheduled_at=scheduled_at, timezone=timezone_name, recurrence=recurrence,
            target_channel=target_channel or "web", status=ReminderStatus.SCHEDULED.value,
            task_id=task_id, project_id=project_id, person_ref=person_ref,
            created_at=now, updated_at=now,
        )
        self.repository.insert(
            "personal_reminders", reminder,
            self.activity("reminder", reminder.id, "created", None, reminder),
        )
        self.sync_workspace()
        self.mark_daily_memory_dirty()
        return reminder

    def update_reminder(self, reminder_id: str, changes: dict, source: str = "user") -> PersonalReminder:
        before = self.require_reminder(reminder_id)
        clean = self.validate_changes(changes, self.reminder_fields)
        if "status" in clean:
            self.validate_status(clean["status"], ReminderStatus)
        for field in ("scheduled_at", "last_delivered_at", "snoozed_until"):
            if field in clean:
                clean[field] = self.normalize_datetime(clean[field], field, required=field == "scheduled_at")
        if clean.get("recurrence"):
            CronSchedule.validate(clean["recurrence"])
        if clean.get("task_id"):
            self.require_task(clean["task_id"])
        if clean.get("project_id"):
            self.require_project(clean["project_id"])
        if "content" in clean:
            self.require_text(clean["content"], "Reminder content")
            clean["content"] = clean["content"].strip()
        after = replace(before, **clean, updated_at=self.now_iso())
        self.repository.update(
            "personal_reminders", before, after,
            self.activity("reminder", reminder_id, "updated", before, after, source),
        )
        self.sync_workspace()
        self.mark_daily_memory_dirty()
        return after

    def snooze_reminder(self, reminder_id: str, until: str) -> PersonalReminder:
        until = self.normalize_datetime(until, "snoozed_until", required=True)
        return self.update_reminder(
            reminder_id,
            {"status": ReminderStatus.SNOOZED.value, "snoozed_until": until},
        )

    def complete_reminder(self, reminder_id: str) -> PersonalReminder:
        return self.update_reminder(reminder_id, {"status": ReminderStatus.COMPLETED.value})

    def list_reminders(self, statuses: list[str] | None = None, limit: int = 100) -> list[PersonalReminder]:
        if statuses:
            for status in statuses:
                self.validate_status(status, ReminderStatus)
        return self.repository.list("personal_reminders", PersonalReminder, self.user_id, statuses, limit)

    def due_reminders(self, before_at: str | None = None, limit: int = 50) -> list[PersonalReminder]:
        boundary = self.normalize_datetime(before_at or self.now_iso(), "before_at", required=True)
        return self.repository.due_reminders(self.user_id, boundary, limit)

    def create_project(
        self,
        name: str,
        goal: str = "",
        next_action: str = "",
        review_cadence: str = "",
    ) -> PersonalProject:
        self.require_text(name, "Project name")
        now = self.now_iso()
        project = PersonalProject(
            id=self.new_id("proj"), user_id=self.user_id, name=name.strip(), goal=goal.strip(),
            status=ProjectStatus.ACTIVE.value, next_action=next_action.strip(),
            review_cadence=review_cadence, created_at=now, updated_at=now,
        )
        self.repository.insert(
            "personal_projects", project,
            self.activity("project", project.id, "created", None, project),
        )
        self.sync_workspace()
        return project

    def update_project(self, project_id: str, changes: dict, source: str = "user") -> PersonalProject:
        before = self.require_project(project_id)
        clean = self.validate_changes(changes, self.project_fields)
        if "status" in clean:
            self.validate_status(clean["status"], ProjectStatus)
        if "last_reviewed_at" in clean:
            clean["last_reviewed_at"] = self.normalize_datetime(clean["last_reviewed_at"], "last_reviewed_at")
        if "name" in clean:
            self.require_text(clean["name"], "Project name")
            clean["name"] = clean["name"].strip()
        after = replace(before, **clean, updated_at=self.now_iso())
        self.repository.update(
            "personal_projects", before, after,
            self.activity("project", project_id, "updated", before, after, source),
        )
        self.sync_workspace()
        return after

    def list_projects(self, statuses: list[str] | None = None, limit: int = 100) -> list[PersonalProject]:
        if statuses:
            for status in statuses:
                self.validate_status(status, ProjectStatus)
        return self.repository.list("personal_projects", PersonalProject, self.user_id, statuses, limit)

    def activity_history(self, limit: int = 100) -> list[dict]:
        return self.repository.activity(self.user_id, limit)

    def context(self, limit: int = 8) -> str:
        tasks = self.list_tasks(
            statuses=[
                PersonalTaskStatus.INBOX.value, PersonalTaskStatus.PLANNED.value,
                PersonalTaskStatus.WAITING.value, PersonalTaskStatus.IN_PROGRESS.value,
                PersonalTaskStatus.DEFERRED.value,
            ],
            limit=limit,
        )
        reminders = self.list_reminders(
            statuses=[ReminderStatus.SCHEDULED.value, ReminderStatus.SNOOZED.value],
            limit=limit,
        )
        projects = self.list_projects(statuses=[ProjectStatus.ACTIVE.value, ProjectStatus.PAUSED.value], limit=limit)
        if not tasks and not reminders and not projects:
            return ""
        lines = ["Current approved personal state:"]
        for task in tasks:
            due = f", due={task.due_at}" if task.due_at else ""
            lines.append(f"- Task [{task.id}] {task.title} ({task.status}{due})")
        for reminder in reminders:
            lines.append(
                f"- Reminder [{reminder.id}] {reminder.content} "
                f"({reminder.status}, at={reminder.snoozed_until or reminder.scheduled_at})"
            )
        for project in projects:
            lines.append(f"- Project [{project.id}] {project.name} ({project.status}, next={project.next_action or 'unset'})")
        return "\n".join(lines)

    def require_task(self, task_id: str) -> PersonalTask:
        task = self.repository.get("personal_tasks", PersonalTask, task_id, self.user_id)
        if task is None:
            raise FileNotFoundError(f"Personal task not found: {task_id}")
        return task

    def require_reminder(self, reminder_id: str) -> PersonalReminder:
        reminder = self.repository.get("personal_reminders", PersonalReminder, reminder_id, self.user_id)
        if reminder is None:
            raise FileNotFoundError(f"Personal reminder not found: {reminder_id}")
        return reminder

    def require_project(self, project_id: str) -> PersonalProject:
        project = self.repository.get("personal_projects", PersonalProject, project_id, self.user_id)
        if project is None:
            raise FileNotFoundError(f"Personal project not found: {project_id}")
        return project

    def sync_workspace(self) -> None:
        self.write_view("TASKS.md", "Personal Tasks", [
            f"- [{item.status}] {item.title} <!-- {item.id} -->"
            for item in self.list_tasks(limit=500)
        ])
        self.write_view("REMINDERS.md", "Reminders", [
            f"- [{item.status}] {item.scheduled_at} {item.content} <!-- {item.id} -->"
            for item in self.list_reminders(limit=500)
        ])
        self.write_view("PROJECTS.md", "Projects", [
            f"- [{item.status}] {item.name}: {item.goal} | next: {item.next_action} <!-- {item.id} -->"
            for item in self.list_projects(limit=500)
        ])

    def write_view(self, name: str, title: str, lines: list[str]) -> None:
        path = self.workspace_dir / name
        temp = path.with_suffix(".md.tmp")
        content = f"# {title}\n\nGenerated from structured personal state.\n\n"
        content += "\n".join(lines) if lines else "(none)"
        temp.write_text(content.rstrip() + "\n", encoding="utf-8")
        temp.replace(path)

    def activity(self, entity_type: str, entity_id: str, operation: str, before, after, source: str = "user") -> dict:
        return {
            "id": self.new_id("act"), "user_id": self.user_id,
            "entity_type": entity_type, "entity_id": entity_id, "operation": operation,
            "before": before.to_dict() if before else None,
            "after": after.to_dict() if after else None,
            "source": source, "created_at": self.now_iso(),
        }

    def validate_changes(self, changes: dict, allowed: set[str]) -> dict:
        if not changes:
            raise ValueError("At least one change is required")
        invalid = sorted(set(changes) - allowed)
        if invalid:
            raise ValueError(f"Unsupported fields: {', '.join(invalid)}")
        return dict(changes)

    def validate_status(self, value: str, enum_type) -> None:
        if value not in {item.value for item in enum_type}:
            raise ValueError(f"Invalid status: {value}")

    def validate_priority(self, value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise ValueError("Priority must be an integer from 1 to 5")

    def validate_datetime(self, value: str, field: str, required: bool = False) -> None:
        self.normalize_datetime(value, field, required)

    def normalize_datetime(self, value: str, field: str, required: bool = False) -> str:
        if not value:
            if required:
                raise ValueError(f"{field} is required")
            return ""
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"{field} must include a timezone offset")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def require_text(self, value: str, label: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} cannot be empty")

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    def mark_daily_memory_dirty(self) -> None:
        self.conversation_memory.mark_daily_needs_rebuild(
            datetime.now().astimezone().date().isoformat()
        )
