from __future__ import annotations

from .retention import ConversationRetentionService
from main.notifications import NotificationOutbox


class MemoryAdminService:
    """Web-facing read/write facade. It never creates sessions or changes conversation identity."""

    def __init__(self, conversation, personal_memory):
        self.conversation = conversation
        self.personal_memory = personal_memory
        self.retention = ConversationRetentionService(conversation.repository, personal_memory)
        self.notifications = NotificationOutbox(conversation.repository.workdir)

    def factual_messages(self, local_date: str = "", limit: int = 100) -> list[dict]:
        records = self.conversation.repository.messages_for_day(local_date, include_redacted=True) if local_date else self.conversation.repository.recent_messages(limit)
        return [{**item.to_dict(), "attachments": self.conversation.repository.attachments(item.message_id)} for item in records]

    def track_messages(
        self,
        *,
        mode: str,
        scope_id: str,
        track_id: str,
        limit: int = 50,
        before_sequence: int | None = None,
    ) -> list[dict]:
        records = self.conversation.repository.track_history(
            mode=mode,
            scope_id=scope_id,
            track_id=track_id,
            limit=limit,
            before_sequence=before_sequence,
            include_redacted=True,
        )
        return [
            {
                **item.to_dict(),
                "attachments": self.conversation.repository.attachments(item.message_id),
            }
            for item in records
        ]

    def daily_memory(self, local_date: str = "") -> dict | None:
        return self.conversation.repository.day(local_date) if local_date else self.conversation.repository.latest_daily_memory()

    def daily_memories(self, limit: int = 100) -> list[dict]:
        return self.conversation.repository.list_days(limit)

    def long_term_memories(self, status: str = "", limit: int = 100) -> list[dict]:
        return [
            {**record.to_dict(), "source_message_ids": self.sources_for_memory(record.id)}
            for record in self.personal_memory.list(status=status, limit=limit)
        ]

    def sources_for_memory(self, memory_id: str) -> list[str]:
        with self.conversation.repository.connect() as connection:
            rows = connection.execute("SELECT message_id FROM long_term_memory_sources WHERE memory_id=? ORDER BY message_id", (memory_id,)).fetchall()
        return [row["message_id"] for row in rows]

    def notification_status(self, limit: int = 100) -> list[dict]:
        return self.notifications.list(limit)
