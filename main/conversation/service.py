from __future__ import annotations

import json
from datetime import datetime

from .repository import ConversationMemoryRepository
from .daily_memory_generator import DailyMemoryGenerator


class ConversationMemoryService:
    max_inline_tool_chars = 8000
    def __init__(self, repository: ConversationMemoryRepository, personal_state=None):
        self.repository = repository
        self.personal_state = personal_state
        self.last_recent_ids: list[str] = []
        self.last_daily_ids: list[str] = []
        self.daily_generator = DailyMemoryGenerator()

    def append_runtime_messages(self, messages: list, start_index: int, timezone_name: str) -> list[str]:
        stored_ids = []
        reply_to = ""
        for message in messages[start_index:]:
            role = str(message.get("role", "system"))
            content = self.archive_content(message.get("content", ""))
            if content is None:
                continue
            factual_role = self.factual_role(role, content)
            stored = self.repository.append_message(
                factual_role, content, timezone_name=timezone_name, reply_to_message_id=reply_to,
                metadata={"runtime_role": role},
            )
            if factual_role == "tool" and len(json.dumps(content, ensure_ascii=False, default=str)) > self.max_inline_tool_chars:
                attachment_id = self.repository.store_attachment(stored.message_id, content)
                stored.content = self.tool_summary(content, attachment_id)
                self.repository.replace_message_content(stored.message_id, stored.content)
            stored_ids.append(stored.message_id)
            if factual_role == "user":
                reply_to = stored.message_id
        return stored_ids

    def recent_context(self, limit: int = 8) -> str:
        records = self.repository.recent_messages(limit)
        self.last_recent_ids = [record.message_id for record in records]
        if not records:
            return ""
        lines = ["<recent_factual_context>"]
        for item in records:
            text = self.text(item.content)[:700]
            if text:
                lines.append(f"- [{item.role} #{item.message_id}] {text[:700]}")
        lines.append("</recent_factual_context>")
        return "\n".join(lines)

    def current_daily_context(self) -> str:
        daily = self.repository.latest_daily_memory()
        self.last_daily_ids = []
        if not daily or not daily.get("summary"):
            return ""
        source_ids = ",".join(self.repository.daily_sources(daily["local_date"])[:20])
        self.last_daily_ids = self.repository.daily_sources(daily["local_date"])
        return f"<daily_memory date=\"{daily['local_date']}\" source_message_ids=\"{source_ids}\">\n{daily['summary'][:1800]}\n</daily_memory>"

    def generate_daily_memory(self, local_date: str) -> str:
        messages = self.repository.messages_for_day(local_date)
        source_ids = [item.message_id for item in messages]
        narrative = []
        important_events, open_loops, emotional_signals = [], [], []
        for item in messages:
            text = self.text(item.content)
            if text and item.role in {"user", "assistant"}:
                narrative.append(f"{item.role}: {text[:300]}")
                if item.role == "user" and any(word in text for word in ("决定", "记住", "开始", "完成")):
                    important_events.append(text[:240])
                if item.role == "user" and any(word in text for word in ("稍后", "还没", "待", "问题", "阻塞")):
                    open_loops.append(text[:240])
                if item.role == "user" and any(word in text for word in ("开心", "焦虑", "难过", "压力", "疲惫")):
                    emotional_signals.append(text[:160])
        summary = self.narrative_summary(narrative)
        task_changes = self.task_changes(local_date)
        fingerprint = self.daily_generator.fingerprint(messages, task_changes)
        existing = self.repository.day(local_date)
        if existing and existing.get("daily_memory_status") == "generated" and existing.get("input_fingerprint") == fingerprint:
            return existing["summary"]
        daily = {
            "date": local_date, "summary": summary, "important_events": important_events[:12],
            "open_loops": open_loops[:12], "task_changes": task_changes, "emotional_signals": emotional_signals[:8],
            "source_message_ids": source_ids, "generated_at": datetime.now().astimezone().isoformat(), "status": "generated",
        }
        self.daily_generator.validate(daily)
        self.repository.upsert_daily_memory(local_date, daily, source_ids, fingerprint)
        self.sync_daily_view(daily)
        return summary

    def rebuild_pending_days(self) -> int:
        exported = self.repository.export()
        dates = {item["day_date"] for item in exported}
        count = 0
        for local_date in dates:
            day = self.repository.day(local_date)
            if day and day["daily_memory_status"] in {"open", "needs_rebuild", "failed"}:
                try:
                    self.generate_daily_memory(local_date)
                    count += 1
                except Exception as exc:
                    self.repository.mark_daily_failed(local_date, f"{type(exc).__name__}: {exc}")
        return count

    def rebuild_prior_days(self, timezone_name: str) -> int:
        today = datetime.now().astimezone().date().isoformat()
        count = 0
        for local_date in {item["day_date"] for item in self.repository.export()}:
            day = self.repository.day(local_date)
            if local_date < today and day and day["daily_memory_status"] in {"open", "needs_rebuild", "failed"}:
                try:
                    self.generate_daily_memory(local_date)
                    count += 1
                except Exception as exc:
                    self.repository.mark_daily_failed(local_date, f"{type(exc).__name__}: {exc}")
        return count

    def factual_role(self, role: str, content: object) -> str:
        if role == "assistant":
            return "assistant"
        if role == "user" and self.is_tool_result(content):
            return "tool"
        if role == "user":
            return "user"
        return "system"

    def is_tool_result(self, content: object) -> bool:
        items = content if isinstance(content, list) else []
        return any((isinstance(item, dict) and item.get("type") == "tool_result") or getattr(item, "type", "") == "tool_result" for item in items)

    def text(self, value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [self.text(item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")) for item in value]
            return " ".join(part for part in parts if part)
        return json.dumps(value, ensure_ascii=False, default=str)

    def archive_content(self, value: object):
        if isinstance(value, list):
            kept = []
            for block in value:
                block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
                if block_type in {"thinking", "reasoning"}:
                    continue
                kept.append(block)
            return kept or None
        return value

    def tool_summary(self, content: object, attachment_id: str) -> dict:
        text = self.text(content)
        return {"summary": text[:1200], "status": "completed", "attachment_id": attachment_id}

    def narrative_summary(self, lines: list[str]) -> str:
        if not lines:
            return "No factual conversation was recorded."
        # A bounded narrative is a summary, while complete raw facts remain in conversation_messages.
        return " ".join(lines[-8:])[:2400]

    def sync_daily_view(self, daily: dict) -> None:
        path = self.repository.workdir / "workspace" / "memory" / f"{daily['date']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "# Daily Memory - {date}\n\n```json\n{body}\n```\n".format(
            date=daily["date"], body=json.dumps(daily, ensure_ascii=False, indent=2)
        )
        temp = path.with_suffix(".md.tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)

    def task_changes(self, local_date: str) -> list[dict]:
        if self.personal_state is None:
            return []
        changes = []
        for task in self.personal_state.list_tasks(limit=500):
            if task.updated_at.startswith(local_date):
                changes.append({"kind": "task", "id": task.id, "title": task.title, "status": task.status})
        for reminder in self.personal_state.list_reminders(limit=500):
            if reminder.updated_at.startswith(local_date):
                changes.append({"kind": "reminder", "id": reminder.id, "content": reminder.content, "status": reminder.status})
        return changes[:100]
