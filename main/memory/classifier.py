from __future__ import annotations

from .extractor import MemoryCandidate


class CandidateClassifier:
    def classify(self, candidate: MemoryCandidate) -> dict:
        memory_type = candidate.memory_type if candidate.memory_type in {
            "profile_fact", "preference", "goal", "relationship", "event", "note", "reflection",
            "project_knowledge", "procedure", "user_feedback",
        } else "note"
        return {
            "content": candidate.content,
            "memory_type": memory_type,
            "explicit": candidate.explicit,
            "importance": candidate.importance,
            "confidence": candidate.confidence,
            "tags": candidate.tags or [],
            "entity_refs": candidate.entity_refs or [],
            "source_message_ids": candidate.source_message_ids,
            "privacy_level": "normal",
            "retention_policy": "permanent",
            "origin": "explicit_user" if candidate.explicit else "model_inference",
        }
