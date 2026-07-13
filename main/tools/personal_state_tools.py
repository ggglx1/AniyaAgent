import json

from main.personal.manager import PersonalStateManager


class PersonalStateTool:
    def json(self, value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)


class CreatePersonalTaskTool(PersonalStateTool):
    name = "create_personal_task"
    definition = {
        "name": name,
        "description": (
            "Create a durable personal task or commitment. Use this for the user's life/work items; "
            "do not use the Agent Team create_task engineering board for personal tasks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"}, "description": {"type": "string"},
                "status": {"type": "string", "enum": ["inbox", "planned", "waiting", "in_progress", "deferred"]},
                "priority": {"type": "integer"}, "project_id": {"type": "string"},
                "due_at": {"type": "string"}, "next_action": {"type": "string"},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "source_conversation": {"type": "string"},
            },
            "required": ["title"],
        },
    }

    def __init__(self, manager): self.manager = manager

    def run(self, title: str, description: str = "", status: str = "inbox", priority: int = 3,
            project_id: str = "", due_at: str = "", next_action: str = "",
            blockers: list[str] | None = None, source_conversation: str = "") -> str:
        return self.json(self.manager.create_task(
            title, description, status, priority, project_id, due_at,
            next_action, blockers, source_conversation,
        ).to_dict())


class ListPersonalTasksTool(PersonalStateTool):
    name = "list_personal_tasks"
    definition = {
        "name": name,
        "description": "List durable personal tasks, optionally filtered by lifecycle status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "statuses": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, statuses: list[str] | None = None, limit: int = 100) -> str:
        return self.json([item.to_dict() for item in self.manager.list_tasks(statuses, limit)])


class UpdatePersonalTaskTool(PersonalStateTool):
    name = "update_personal_task"
    definition = {
        "name": name,
        "description": "Update a personal task's schedule, priority, status, project, blocker, or next action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "changes": {"type": "object", "additionalProperties": True},
            },
            "required": ["task_id", "changes"],
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, task_id: str, changes: dict) -> str:
        return self.json(self.manager.update_task(task_id, changes).to_dict())


class CompletePersonalTaskTool(PersonalStateTool):
    name = "complete_personal_task"
    definition = {
        "name": name,
        "description": "Mark a personal task done and optionally save a completion note.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "note": {"type": "string"}},
            "required": ["task_id"],
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, task_id: str, note: str = "") -> str:
        return self.json(self.manager.complete_task(task_id, note).to_dict())


class CreateReminderTool(PersonalStateTool):
    name = "create_reminder"
    definition = {
        "name": name,
        "description": (
            "Create a structured personal reminder. scheduled_at must be an explicit ISO-8601 datetime. "
            "If the user's time is ambiguous, ask before calling this tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"}, "scheduled_at": {"type": "string"},
                "timezone_name": {"type": "string"}, "recurrence": {"type": "string"},
                "target_channel": {"type": "string"}, "task_id": {"type": "string"},
                "project_id": {"type": "string"}, "person_ref": {"type": "string"},
            },
            "required": ["content", "scheduled_at"],
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, content: str, scheduled_at: str, timezone_name: str = "Asia/Shanghai",
            recurrence: str = "", target_channel: str = "web", task_id: str = "",
            project_id: str = "", person_ref: str = "") -> str:
        return self.json(self.manager.create_reminder(
            content, scheduled_at, timezone_name, recurrence, target_channel,
            task_id, project_id, person_ref,
        ).to_dict())


class ListRemindersTool(PersonalStateTool):
    name = "list_reminders"
    definition = {
        "name": name,
        "description": "List structured reminders and their delivery lifecycle status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "statuses": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, statuses: list[str] | None = None, limit: int = 100) -> str:
        return self.json([item.to_dict() for item in self.manager.list_reminders(statuses, limit)])


class UpdateReminderTool(PersonalStateTool):
    name = "update_reminder"
    definition = {
        "name": name,
        "description": "Edit a reminder's content, time, recurrence, channel, links, or status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string"},
                "changes": {"type": "object", "additionalProperties": True},
            },
            "required": ["reminder_id", "changes"],
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, reminder_id: str, changes: dict) -> str:
        return self.json(self.manager.update_reminder(reminder_id, changes).to_dict())


class SnoozeReminderTool(PersonalStateTool):
    name = "snooze_reminder"
    definition = {
        "name": name,
        "description": "Snooze a reminder until a new explicit ISO-8601 datetime.",
        "input_schema": {
            "type": "object",
            "properties": {"reminder_id": {"type": "string"}, "until": {"type": "string"}},
            "required": ["reminder_id", "until"],
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, reminder_id: str, until: str) -> str:
        return self.json(self.manager.snooze_reminder(reminder_id, until).to_dict())


class CompleteReminderTool(PersonalStateTool):
    name = "complete_reminder"
    definition = {
        "name": name,
        "description": "Mark a reminder completed so it will no longer be delivered.",
        "input_schema": {
            "type": "object", "properties": {"reminder_id": {"type": "string"}},
            "required": ["reminder_id"],
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, reminder_id: str) -> str:
        return self.json(self.manager.complete_reminder(reminder_id).to_dict())


class CreatePersonalProjectTool(PersonalStateTool):
    name = "create_personal_project"
    definition = {
        "name": name,
        "description": "Create a long-running personal or work project with a goal and next action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"}, "goal": {"type": "string"},
                "next_action": {"type": "string"}, "review_cadence": {"type": "string"},
            },
            "required": ["name"],
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, name: str, goal: str = "", next_action: str = "", review_cadence: str = "") -> str:
        return self.json(self.manager.create_project(name, goal, next_action, review_cadence).to_dict())


class ListPersonalProjectsTool(PersonalStateTool):
    name = "list_personal_projects"
    definition = {
        "name": name,
        "description": "List personal projects and their current next actions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "statuses": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, statuses: list[str] | None = None, limit: int = 100) -> str:
        return self.json([item.to_dict() for item in self.manager.list_projects(statuses, limit)])


class UpdatePersonalProjectTool(PersonalStateTool):
    name = "update_personal_project"
    definition = {
        "name": name,
        "description": "Update a project's goal, status, next action, blockers, decisions, or review schedule.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "changes": {"type": "object", "additionalProperties": True},
            },
            "required": ["project_id", "changes"],
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, project_id: str, changes: dict) -> str:
        return self.json(self.manager.update_project(project_id, changes).to_dict())


class ListPersonalActivityTool(PersonalStateTool):
    name = "list_personal_activity"
    definition = {
        "name": name,
        "description": "Inspect recent auditable changes to personal tasks, reminders, and projects.",
        "input_schema": {
            "type": "object", "properties": {"limit": {"type": "integer"}},
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, limit: int = 100) -> str:
        return self.json(self.manager.activity_history(limit))


def build_personal_state_tools(manager: PersonalStateManager) -> list:
    return [
        CreatePersonalTaskTool(manager), ListPersonalTasksTool(manager),
        UpdatePersonalTaskTool(manager), CompletePersonalTaskTool(manager),
        CreateReminderTool(manager), ListRemindersTool(manager), UpdateReminderTool(manager),
        SnoozeReminderTool(manager), CompleteReminderTool(manager),
        CreatePersonalProjectTool(manager), ListPersonalProjectsTool(manager),
        UpdatePersonalProjectTool(manager), ListPersonalActivityTool(manager),
    ]
