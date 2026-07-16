from __future__ import annotations

from datetime import datetime


class CandidateValidator:
    allowed_types = {"profile_fact", "preference", "goal", "relationship", "event", "note", "reflection", "project_knowledge", "procedure", "user_feedback"}
    allowed_privacy = {"normal", "sensitive", "restricted"}

    def __init__(self, conversation_repository):
        self.repository = conversation_repository

    def validate(self, candidate: dict, allowed_source_ids: set[str]) -> dict | None:
        if candidate.get("memory_type") not in self.allowed_types:
            return None
        if candidate.get("privacy_level", "normal") not in self.allowed_privacy:
            return None
        if not isinstance(candidate.get("content"), str) or not candidate["content"].strip():
            return None
        try:
            candidate["importance"] = max(0.0, min(float(candidate.get("importance", 0.5)), 1.0))
            candidate["confidence"] = max(0.0, min(float(candidate.get("confidence", 0.5)), 1.0))
        except (TypeError, ValueError):
            return None
        source_ids = [item for item in candidate.get("source_message_ids", []) if item in allowed_source_ids]
        if not source_ids or any(not self.repository.message_is_active(item) for item in source_ids):
            return None
        valid_until = str(candidate.get("valid_until") or "")
        if valid_until:
            try:
                datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            except ValueError:
                return None
        candidate["source_message_ids"] = source_ids
        candidate["valid_until"] = valid_until
        return candidate
