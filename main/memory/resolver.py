from __future__ import annotations

from .manager import PersonalMemoryManager


class MemoryResolver:
    def __init__(self, manager: PersonalMemoryManager):
        self.manager = manager

    def resolve(self, candidate: dict, user_id: str = "local") -> tuple[str, object | None]:
        normalized = candidate["content"].casefold().strip()
        active = self.manager.list(user_id=user_id, status="active", limit=200)
        for record in active:
            if record.content.casefold().strip() == normalized:
                return "duplicate", record
            if record.type == candidate["memory_type"] and self.same_subject(record.content, candidate["content"]):
                return "conflict", record
        return "new", None

    def same_subject(self, old: str, new: str) -> bool:
        keys = ("名字", "偏好", "喜欢", "语言", "时区")
        return any(key in old and key in new for key in keys)
