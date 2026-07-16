from __future__ import annotations

import json
import os

from .extractor import MemoryCandidate


class ControlledLlmMemoryExtractor:
    """Optional semantic candidate extractor. It never receives repository write access."""

    def __init__(self, client=None, model: str = ""):
        self.client = client
        self.model = model
        self.enabled = os.getenv("MEMORY_EXTRACTION_LLM_ENABLED", "false").strip().lower() == "true"

    def extract(self, messages: list) -> list[MemoryCandidate]:
        if not self.enabled or self.client is None or not messages:
            return []
        facts = [
            {"message_id": item.message_id, "role": item.role, "content": self.text(item.content)[:1000]}
            for item in messages if item.role in {"user", "assistant"}
        ]
        if not facts:
            return []
        prompt = (
            "Extract only durable personal-memory candidates from factual conversation JSON. "
            "Never return tasks, reminders, deadlines, credentials, or sensitive data. "
            "Return JSON array of {content,type,importance,confidence,explicit,source_message_ids}. "
            "Inferred items must set explicit=false.\n" + json.dumps(facts, ensure_ascii=False)
        )
        try:
            response = self.client.messages.create(
                task_type="memory_extract", model=self.model, system="Return valid JSON only.",
                messages=[{"role": "user", "content": prompt}], tools=[], max_tokens=800,
            )
            text = self.text(response.content)
            data = json.loads(text[text.find("["):text.rfind("]") + 1])
        except Exception:
            return []
        candidates = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or not str(item.get("content") or "").strip():
                continue
            candidates.append(MemoryCandidate(
                content=str(item["content"]).strip(), explicit=bool(item.get("explicit", False)),
                source_message_ids=[str(value) for value in item.get("source_message_ids", [])],
                memory_type=str(item.get("type") or "note"), importance=float(item.get("importance", 0.5)),
                confidence=float(item.get("confidence", 0.5)),
            ))
        return candidates

    def text(self, value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(str(getattr(item, "text", "") or (item.get("text", "") if isinstance(item, dict) else "")) for item in value)
        return str(value)
