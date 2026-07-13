import json

from main.assistant.profile import ProfileStore
from main.memory.manager import PersonalMemoryManager


class PersonalTool:
    def json(self, value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)


class GetProfileTool(PersonalTool):
    name = "get_profile"
    definition = {
        "name": name,
        "description": "Read the user's explicit personal-assistant profile and preferences.",
        "input_schema": {"type": "object", "properties": {}},
    }

    def __init__(self, store: ProfileStore):
        self.store = store

    def run(self) -> str:
        return self.json(self.store.get())


class UpdateProfileTool(PersonalTool):
    name = "update_profile"
    definition = {
        "name": name,
        "description": (
            "Update explicit profile fields after the user states or requests the change. "
            "Do not store tasks, reminders, guesses, or temporary conversation details here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "object",
                    "properties": {
                        "display_name": {"type": "string"},
                        "preferred_address": {"type": "string"},
                        "language": {"type": "string"},
                        "communication_style": {"type": "string"},
                        "timezone": {"type": "string"},
                        "work_hours": {"type": "object", "additionalProperties": True},
                        "quiet_hours": {"type": "object", "additionalProperties": True},
                        "reminder_preferences": {"type": "object", "additionalProperties": True},
                        "planning_preferences": {"type": "object", "additionalProperties": True},
                        "assistant_feedback": {"type": "array", "items": {"type": "string"}},
                        "proactive_paused": {"type": "boolean"},
                    },
                }
            },
            "required": ["changes"],
        },
    }

    def __init__(self, store: ProfileStore):
        self.store = store

    def run(self, changes: dict) -> str:
        return self.json(self.store.update(changes))


class RememberPersonalFactTool(PersonalTool):
    name = "remember_personal_fact"
    definition = {
        "name": name,
        "description": (
            "Store a durable personal fact, preference, goal, relationship, event, note, procedure, "
            "project knowledge, or user feedback. Set explicit=false for model inference so it remains "
            "pending confirmation. Never use this for a task, promise, deadline, or reminder; use the "
            "structured task/reminder system instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "memory_type": {
                    "type": "string",
                    "enum": [
                        "profile_fact", "preference", "goal", "relationship", "event", "note",
                        "reflection", "project_knowledge", "procedure", "user_feedback"
                    ],
                },
                "explicit": {"type": "boolean"},
                "importance": {"type": "number"},
                "confidence": {"type": "number"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content", "memory_type", "explicit"],
        },
    }

    def __init__(self, manager: PersonalMemoryManager):
        self.manager = manager

    def run(
        self,
        content: str,
        memory_type: str,
        explicit: bool,
        importance: float = 0.5,
        confidence: float = 1.0,
        tags: list[str] | None = None,
    ) -> str:
        record = self.manager.add(
            content=content,
            memory_type=memory_type,
            explicit=explicit,
            importance=importance,
            confidence=confidence,
            tags=tags,
            reason="stored through personal assistant tool",
        )
        return self.json(record.to_dict())


class ListPersonalMemoriesTool(PersonalTool):
    name = "list_personal_memories"
    definition = {
        "name": name,
        "description": "List structured personal memories, including pending confirmations when requested.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "memory_type": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    }

    def __init__(self, manager: PersonalMemoryManager):
        self.manager = manager

    def run(self, status: str = "", memory_type: str = "", limit: int = 50) -> str:
        return self.json([
            item.to_dict()
            for item in self.manager.list(status=status, memory_type=memory_type, limit=limit)
        ])


class ConfirmPersonalMemoryTool(PersonalTool):
    name = "confirm_personal_memory"
    definition = {
        "name": name,
        "description": "Confirm a pending inferred personal memory after the user approves it.",
        "input_schema": {
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"],
        },
    }

    def __init__(self, manager: PersonalMemoryManager):
        self.manager = manager

    def run(self, memory_id: str) -> str:
        return self.json(self.manager.confirm(memory_id).to_dict())


class CorrectPersonalMemoryTool(PersonalTool):
    name = "correct_personal_memory"
    definition = {
        "name": name,
        "description": "Correct an existing memory by superseding it while preserving an audit chain.",
        "input_schema": {
            "type": "object",
            "properties": {"memory_id": {"type": "string"}, "content": {"type": "string"}},
            "required": ["memory_id", "content"],
        },
    }

    def __init__(self, manager: PersonalMemoryManager):
        self.manager = manager

    def run(self, memory_id: str, content: str) -> str:
        return self.json(self.manager.supersede(memory_id, content).to_dict())


class ForgetPersonalMemoryTool(PersonalTool):
    name = "forget_personal_memory"
    definition = {
        "name": name,
        "description": "Forget a personal memory immediately and remove its content from retrieval and audit snapshots.",
        "input_schema": {
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"],
        },
    }

    def __init__(self, manager: PersonalMemoryManager):
        self.manager = manager

    def run(self, memory_id: str) -> str:
        return self.json(self.manager.forget(memory_id).to_dict())


class GetMemoryHistoryTool(PersonalTool):
    name = "get_memory_history"
    definition = {
        "name": name,
        "description": "Inspect the auditable lifecycle history of one personal memory.",
        "input_schema": {
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"],
        },
    }

    def __init__(self, manager: PersonalMemoryManager):
        self.manager = manager

    def run(self, memory_id: str) -> str:
        return self.json([item.__dict__ for item in self.manager.history(memory_id)])


def build_personal_tools(profile_store: ProfileStore, memory_manager: PersonalMemoryManager) -> list:
    return [
        GetProfileTool(profile_store),
        UpdateProfileTool(profile_store),
        RememberPersonalFactTool(memory_manager),
        ListPersonalMemoriesTool(memory_manager),
        ConfirmPersonalMemoryTool(memory_manager),
        CorrectPersonalMemoryTool(memory_manager),
        ForgetPersonalMemoryTool(memory_manager),
        GetMemoryHistoryTool(memory_manager),
    ]
