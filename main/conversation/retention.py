from __future__ import annotations

from .repository import ConversationMemoryRepository


class ConversationRetentionService:
    """Privacy boundary for factual conversation data; sequence numbers are never rewritten."""

    def __init__(self, repository: ConversationMemoryRepository, personal_memory=None):
        self.repository = repository
        self.personal_memory = personal_memory

    def redact(self, message_id: str) -> None:
        linked_memory_ids = self.repository.linked_long_term_memory_ids(message_id)
        self.repository.redact_message(message_id)
        self.repository.invalidate_message_sources(message_id)
        # A fact without its only raw source must not remain silently trusted.
        if self.personal_memory is not None:
            for memory_id in linked_memory_ids:
                try:
                    record = self.personal_memory.require(memory_id, "local")
                    if record.origin == "explicit_user" or self.repository.valid_source_count(memory_id) > 0:
                        continue
                    self.personal_memory.archive(memory_id, reason="all factual sources were redacted")
                except FileNotFoundError:
                    pass

    def export(self) -> list[dict]:
        return self.repository.export()
