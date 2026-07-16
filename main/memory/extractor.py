from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class MemoryCandidate:
    content: str
    explicit: bool
    source_message_ids: list[str]
    memory_type: str = "note"
    importance: float = 0.6
    confidence: float = 0.85
    tags: list[str] | None = None
    entity_refs: list[str] | None = None


class MemoryExtractor:
    """Deterministic first-pass extractor. Normal conversations never depend on an LLM here."""

    explicit_patterns = (
        r"(?:请)?记住[：:，,]?\s*(.+)", r"我(?:喜欢|偏好|希望|习惯)[：:，,]?\s*(.+)",
        r"以后(?:请|都)?[：:，,]?\s*(.+)", r"我的名字(?:是|叫)[：:，,]?\s*(.+)",
    )

    def extract(self, messages: list) -> list[MemoryCandidate]:
        candidates = []
        for item in messages:
            if item.role != "user":
                continue
            text = self.text(item.content)
            if self.is_task_or_reminder(text):
                continue
            for pattern in self.explicit_patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if not match:
                    continue
                content = match.group(1 if match.lastindex == 1 else match.lastindex).strip(" 。.！!？?")
                if len(content) >= 2:
                    candidates.append(MemoryCandidate(
                        content=content, explicit=True, source_message_ids=[item.message_id],
                        memory_type=self.memory_type(text), tags=self.tags(content), entity_refs=self.entities(content),
                    ))
                break
        return candidates

    def text(self, value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(self.text(getattr(item, "text", item.get("text", "") if isinstance(item, dict) else "")) for item in value)
        return str(value)

    def is_task_or_reminder(self, text: str) -> bool:
        return any(word in text for word in ("提醒我", "待办", "todo", "截止", "明天", "周一", "任务"))

    def memory_type(self, text: str) -> str:
        if "名字" in text:
            return "profile_fact"
        if any(word in text for word in ("喜欢", "偏好", "希望", "习惯")):
            return "preference"
        return "note"

    def tags(self, text: str) -> list[str]:
        return list({token.lower() for token in re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text)})[:8]

    def entities(self, text: str) -> list[str]:
        return [token for token in re.findall(r"[A-Z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", text)][:8]
