from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    name: str
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()
    risk_level: str = "read_only"
    requires_confirmation: bool = False


class ActionRegistry:
    """The canonical contract shared by structured parsing, routing and execution."""

    _SPECS = (
        ActionSpec("task.create", ("title",), ("description", "priority", "due_at"), "write"),
        ActionSpec("task.query"),
        ActionSpec("task.update", ("id",), ("title", "description", "priority", "due_at", "next_action", "status"), "write"),
        ActionSpec("task.complete", ("id",), risk_level="write"),
        ActionSpec("task.cancel", ("id",), risk_level="write"),
        ActionSpec("task.delete", ("id",), risk_level="write_irreversible", requires_confirmation=True),
        ActionSpec("reminder.create", ("content", "scheduled_at"), ("timezone", "target_channel", "recurrence"), "write"),
        ActionSpec("reminder.query"),
        ActionSpec("reminder.update", ("id",), ("content", "scheduled_at", "timezone", "target_channel", "recurrence", "status"), "write"),
        ActionSpec("reminder.snooze", ("id", "scheduled_at"), risk_level="write"),
        ActionSpec("reminder.cancel", ("id",), risk_level="write"),
        ActionSpec("reminder.delete", ("id",), risk_level="write_irreversible", requires_confirmation=True),
        ActionSpec("routine.create", ("name", "routine_type", "cron"), ("timezone", "target_channel"), "write"),
        ActionSpec("routine.query"),
        ActionSpec("routine.update", ("id",), ("name", "routine_type", "cron", "timezone", "target_channel", "enabled"), "write"),
        ActionSpec("routine.pause", ("id",), risk_level="write"),
        ActionSpec("routine.resume", ("id",), risk_level="write"),
        ActionSpec("routine.delete", ("id",), risk_level="write_irreversible", requires_confirmation=True),
        ActionSpec("memory.confirm", ("id",), risk_level="write"),
        ActionSpec("memory.correct", ("id", "replacement_text"), risk_level="write"),
        ActionSpec("memory.archive", ("id",), risk_level="write"),
        ActionSpec("memory.forget", ("id",), risk_level="write_irreversible", requires_confirmation=True),
    )
    _BY_NAME = {item.name: item for item in _SPECS}

    @classmethod
    def get(cls, action: str) -> ActionSpec | None:
        return cls._BY_NAME.get(action)

    @classmethod
    def supports(cls, action: str) -> bool:
        return action in cls._BY_NAME

    @classmethod
    def missing(cls, action: str, arguments: dict) -> list[str]:
        spec = cls.get(action)
        if spec is None:
            return ["unsupported_action"]
        return [field for field in spec.required_fields if not str(arguments.get(field) or "").strip()]
