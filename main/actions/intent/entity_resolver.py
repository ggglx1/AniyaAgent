from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntityResolution:
    entity_id: str = ""
    ambiguity: str = ""
    candidates: tuple[str, ...] = ()


class EntityResolver:
    prefixes = {"task": "ptask_", "reminder": "rem_", "routine": "routine_", "memory": "mem_"}

    def __init__(self, application): self.app = application

    def resolve(self, action: str, arguments: dict, raw_text: str = "") -> EntityResolution:
        if action.endswith((".create", ".query")):
            return EntityResolution()
        entity_id = str(arguments.get("id") or "")
        domain = action.split(".", 1)[0]
        if not entity_id:
            candidates = self.name_candidates(domain, raw_text)
            if candidates:
                return EntityResolution(ambiguity="entity_selection_required", candidates=tuple(candidates))
            return EntityResolution(ambiguity="entity_id_required")
        if not entity_id.startswith(self.prefixes.get(domain, "!")):
            return EntityResolution(ambiguity="entity_type_mismatch")
        try:
            if domain == "task": self.app.runtime.personal_state.require_task(entity_id)
            elif domain == "reminder": self.app.runtime.personal_state.require_reminder(entity_id)
            elif domain == "routine": self.app.runtime.routine_manager.require(entity_id)
            else: self.app.runtime.personal_memory_manager.require(entity_id, "local")
        except FileNotFoundError:
            return EntityResolution(ambiguity="entity_not_found")
        return EntityResolution(entity_id=entity_id)

    def name_candidates(self, domain: str, raw_text: str) -> list[str]:
        if not raw_text:
            return []
        if domain == "task":
            return [item.id for item in self.app.runtime.personal_state.list_tasks(limit=100) if item.title and item.title in raw_text]
        if domain == "reminder":
            return [item.id for item in self.app.runtime.personal_state.list_reminders(limit=100) if item.content and item.content in raw_text]
        if domain == "routine":
            return [item.id for item in self.app.runtime.routine_manager.list(limit=100) if item.name and item.name in raw_text]
        return []
