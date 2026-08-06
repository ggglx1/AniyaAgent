from __future__ import annotations

from main.conversation.service import ConversationMemoryService
from datetime import datetime, timezone

from .retriever import PersonalMemoryRetriever


class MemoryContextAssembler:
    """Creates the only memory blocks injected into a normal agent prompt."""

    def __init__(self, conversation: ConversationMemoryService, retriever: PersonalMemoryRetriever):
        self.conversation = conversation
        self.retriever = retriever
        self.last_sources: dict[str, list[str]] = {}
        self.last_manifest: dict = {}

    def assemble(self, query: str, user_id: str = "local", mode: str = "assistant", repository_id: str = "") -> str:
        blocks = [
            self.conversation.recent_context() if mode == "assistant" else "",
            self.conversation.current_daily_context() if mode == "assistant" else "",
            self.retriever.context(query, user_id=user_id, mode=mode, repository_id=repository_id),
        ]
        self.last_sources = {
            "factual_message_ids": list(self.conversation.last_recent_ids),
            "daily_message_ids": list(self.conversation.last_daily_ids),
            "long_term_memory_ids": list(self.retriever.last_retrieved_ids),
        }
        self.last_manifest = {
            "manifest_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "mode": mode,
            "repository_id": repository_id,
            "query_summary": query[:500],
            "sources": self.last_sources,
        }
        return "\n\n".join(block for block in blocks if block)
