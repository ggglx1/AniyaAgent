from dataclasses import dataclass, field
from enum import Enum


class OutcomeType(str, Enum):
    ANSWER = "answer"
    TASK_CHANGED = "task_changed"
    REMINDER_CHANGED = "reminder_changed"
    PROJECT_CHANGED = "project_changed"
    MEMORY_PROPOSED = "memory_proposed"
    MEMORY_CHANGED = "memory_changed"
    ROUTINE_CHANGED = "routine_changed"
    NO_ACTION = "no_action"


@dataclass
class AssistantOutcome:
    type: OutcomeType
    summary: str
    entity_ids: list[str] = field(default_factory=list)
    next_action: str = ""
    requires_confirmation: bool = False
