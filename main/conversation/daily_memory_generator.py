from __future__ import annotations

import hashlib
import json


class DailyMemoryGenerator:
    """Builds one validated, idempotent Daily Memory from factual records only."""

    required_fields = {"date", "summary", "important_events", "open_loops", "task_changes", "emotional_signals", "source_message_ids", "generated_at", "status"}

    def fingerprint(self, messages: list, task_changes: list) -> str:
        source = [
            {"id": item.message_id, "content": item.content, "redacted_at": item.redacted_at}
            for item in messages
        ] + [{"task_changes": task_changes}]
        return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def validate(self, daily: dict) -> None:
        missing = self.required_fields - set(daily)
        if missing:
            raise ValueError(f"Daily Memory missing fields: {sorted(missing)}")
        if not isinstance(daily["summary"], str) or not daily["summary"].strip():
            raise ValueError("Daily Memory summary cannot be empty")
        for field in ("important_events", "open_loops", "task_changes", "emotional_signals", "source_message_ids"):
            if not isinstance(daily[field], list):
                raise ValueError(f"Daily Memory field must be a list: {field}")
