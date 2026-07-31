from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    name: str
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()
    risk_level: str = "read_only"
    requires_confirmation: bool = False
    minimum_confidence: str = "high"
    allow_fast_execute: bool = False

    @property
    def execution_policy(self) -> str:
        if self.requires_confirmation:
            return "irreversible_write"
        if self.risk_level == "read_only":
            return "read_only"
        return "reversible_write"

    @property
    def preview_required(self) -> bool:
        return self.execution_policy != "read_only" and not self.allow_fast_execute


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

    @classmethod
    def validate_arguments(cls, action: str, arguments: dict) -> str:
        spec = cls.get(action)
        if spec is None:
            return "unsupported_action"
        allowed = set(spec.required_fields) | set(spec.optional_fields)
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            return f"unsupported_arguments:{','.join(unknown)}"
        if "priority" in arguments and (not isinstance(arguments["priority"], int) or isinstance(arguments["priority"], bool)):
            return "priority_must_be_integer"
        if "routine_type" in arguments and arguments["routine_type"] not in {"morning_plan", "evening_review", "weekly_review"}:
            return "invalid_routine_type"
        return ""
