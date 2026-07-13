import json
from dataclasses import asdict, dataclass, field
from enum import Enum


class MemoryType(str, Enum):
    PROFILE_FACT = "profile_fact"
    PREFERENCE = "preference"
    GOAL = "goal"
    RELATIONSHIP = "relationship"
    EVENT = "event"
    NOTE = "note"
    REFLECTION = "reflection"
    PROJECT_KNOWLEDGE = "project_knowledge"
    PROCEDURE = "procedure"
    USER_FEEDBACK = "user_feedback"


class MemoryStatus(str, Enum):
    PENDING_CONFIRMATION = "pending_confirmation"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MemorySource(str, Enum):
    CONVERSATION = "conversation"
    USER_SETTING = "user_setting"
    TASK_OUTCOME = "task_outcome"
    SYSTEM_REFLECTION = "system_reflection"
    IMPORT = "import"


@dataclass
class MemoryRecord:
    id: str
    user_id: str
    content: str
    type: str
    status: str
    source: str
    importance: float
    confidence: float
    created_at: str
    updated_at: str
    last_accessed_at: str = ""
    valid_from: str = ""
    valid_until: str = ""
    tags: list[str] = field(default_factory=list)
    entity_refs: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    supersedes_memory_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row) -> "MemoryRecord":
        data = dict(row)
        data["tags"] = json.loads(data.get("tags_json") or "[]")
        data["entity_refs"] = json.loads(data.get("entity_refs_json") or "[]")
        data["metadata"] = json.loads(data.get("metadata_json") or "{}")
        data.pop("tags_json", None)
        data.pop("entity_refs_json", None)
        data.pop("metadata_json", None)
        return cls(**data)


@dataclass
class MemoryHistoryRecord:
    id: str
    memory_id: str
    operation: str
    before_snapshot: dict | None
    after_snapshot: dict | None
    reason: str
    created_at: str
