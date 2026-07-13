from dataclasses import asdict, dataclass, field
from enum import Enum


class PersonalTaskStatus(str, Enum):
    INBOX = "inbox"
    PLANNED = "planned"
    WAITING = "waiting"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"
    DEFERRED = "deferred"


class ReminderStatus(str, Enum):
    SCHEDULED = "scheduled"
    DELIVERED = "delivered"
    SNOOZED = "snoozed"
    COMPLETED = "completed"
    MISSED = "missed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


@dataclass
class PersonalTask:
    id: str
    user_id: str
    title: str
    description: str
    status: str
    priority: int
    project_id: str
    due_at: str
    next_action: str
    blockers: list[str] = field(default_factory=list)
    source_conversation: str = ""
    completion_note: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PersonalReminder:
    id: str
    user_id: str
    content: str
    scheduled_at: str
    timezone: str
    recurrence: str
    target_channel: str
    status: str
    task_id: str = ""
    project_id: str = ""
    person_ref: str = ""
    last_delivered_at: str = ""
    snoozed_until: str = ""
    delivery_result: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PersonalProject:
    id: str
    user_id: str
    name: str
    goal: str
    status: str
    next_action: str
    blockers: list[str] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)
    review_cadence: str = ""
    last_reviewed_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
