from __future__ import annotations

from .retention import ConversationRetentionService
from main.notifications import NotificationOutbox


class MemoryAdminService:
    """Web-facing read/write facade. It never creates sessions or changes conversation identity."""

    def __init__(self, conversation, personal_memory, personal_state=None, routine_manager=None):
        self.conversation = conversation
        self.personal_memory = personal_memory
        self.personal_state = personal_state
        self.routine_manager = routine_manager
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

    def search_messages(self, query: str, mode: str = "assistant", limit: int = 50) -> list[dict]:
        return [{**item.to_dict(), "attachments": self.conversation.repository.attachments(item.message_id)} for item in self.conversation.repository.search_track_messages(query, mode=mode, limit=limit)]

    def daily_memory(self, local_date: str = "") -> dict | None:
        return self.conversation.repository.day(local_date) if local_date else self.conversation.repository.latest_daily_memory()

    def daily_memories(self, limit: int = 100) -> list[dict]:
        return self.conversation.repository.list_days(limit)

    def rebuild_daily_memory(self, local_date: str) -> dict:
        self.conversation.repository.mark_daily_needs_rebuild(local_date)
        return {"date": local_date, "summary": self.conversation.generate_daily_memory(local_date), "status": "generated"}

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

    def plans(self, limit: int = 100) -> dict:
        if self.personal_state is None or self.routine_manager is None:
            raise RuntimeError("Personal plan services are unavailable")
        return {
            "tasks": [item.to_dict() for item in self.personal_state.list_tasks(limit=limit)],
            "reminders": [item.to_dict() for item in self.personal_state.list_reminders(limit=limit)],
            "routines": [item.to_dict() for item in self.routine_manager.list(limit=limit)],
        }

    def plan_action(self, payload: dict) -> dict:
        if self.personal_state is None or self.routine_manager is None:
            raise RuntimeError("Personal plan services are unavailable")
        entity = str(payload.get("entity") or "")
        action = str(payload.get("action") or "")
        entity_id = str(payload.get("id") or "")
        if entity == "task":
            if action == "create":
                result = self.personal_state.create_task(
                    str(payload.get("title") or ""),
                    description=str(payload.get("description") or ""),
                    priority=int(payload.get("priority") or 3),
                    due_at=str(payload.get("due_at") or ""),
                )
            elif action == "complete": result = self.personal_state.complete_task(entity_id)
            elif action == "cancel": result = self.personal_state.update_task(entity_id, {"status": "cancelled"})
            elif action == "reopen": result = self.personal_state.update_task(entity_id, {"status": "planned"})
            else: raise ValueError("Unsupported task action")
        elif entity == "reminder":
            if action == "create":
                result = self.personal_state.create_reminder(
                    str(payload.get("content") or ""),
                    str(payload.get("scheduled_at") or ""),
                    timezone_name=str(payload.get("timezone") or "Asia/Shanghai"),
                    recurrence=str(payload.get("recurrence") or ""),
                    target_channel=str(payload.get("target_channel") or "weixin"),
                )
            elif action == "complete": result = self.personal_state.complete_reminder(entity_id)
            elif action == "cancel": result = self.personal_state.update_reminder(entity_id, {"status": "cancelled"})
            else: raise ValueError("Unsupported reminder action")
        elif entity == "routine":
            if action == "create":
                result = self.routine_manager.create(
                    str(payload.get("name") or ""), str(payload.get("routine_type") or ""),
                    str(payload.get("cron") or ""), timezone_name=str(payload.get("timezone") or "Asia/Shanghai"),
                    target_channel=str(payload.get("target_channel") or "weixin"), enabled=bool(payload.get("enabled", True)),
                )
            elif action == "toggle":
                current = self.routine_manager.require(entity_id)
                result = self.routine_manager.update(entity_id, {"enabled": not current.enabled})
            else: raise ValueError("Unsupported routine action")
        else:
            raise ValueError("Unsupported plan entity")
        return {"entity": entity, "item": result.to_dict()}

    def weixin_binding(self) -> dict | None:
        binding = self.notifications.binding("local")
        if not binding:
            return None
        return {
            "owner_id": binding.get("owner_id", "local"),
            "channel_id": binding.get("channel_id", "weixin"),
            "recipient_id": binding.get("recipient_id", ""),
            "status": binding.get("status", ""),
            "verified_at": binding.get("verified_at", ""),
        }

    def issue_weixin_binding_code(self) -> str:
        return self.notifications.issue_binding_code("local", "weixin")

    def invalidate_weixin_binding(self) -> bool:
        return self.notifications.invalidate_binding("local")
