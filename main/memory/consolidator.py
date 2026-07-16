from __future__ import annotations

from main.conversation.service import ConversationMemoryService

from .manager import PersonalMemoryManager


class MemoryConsolidator:
    def __init__(self, conversation: ConversationMemoryService, manager: PersonalMemoryManager):
        self.conversation = conversation
        self.manager = manager

    def daily(self, local_date: str) -> str:
        return self.conversation.generate_daily_memory(local_date)

    def weekly_reflection(self, text: str, source_message_ids: list[str]) -> str:
        record = self.manager.add(
            text, memory_type="reflection", source="weekly_reflection", explicit=False,
            importance=0.5, confidence=0.5, origin="model_inference", metadata={"source_message_ids": source_message_ids},
            reason="weekly reflection proposal",
        )
        self.conversation.repository.link_long_term_memory(record.id, source_message_ids, "inferred_from")
        return record.id
