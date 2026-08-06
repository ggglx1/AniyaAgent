from __future__ import annotations

"""Controlled lifecycle for extracted long-term-memory candidates."""

from .manager import PersonalMemoryManager
from .models import MemoryStatus


class MemoryCandidateService:
    VALID = {"candidate", "pending_confirmation", "active", "rejected", "expired", "invalidated", "duplicate"}

    def __init__(self, manager: PersonalMemoryManager):
        self.manager = manager
        self.repository = manager.repository

    def propose(self, payload: dict, *, user_id: str, source_fingerprint: str, dedupe_key: str, extractor_version: str, conflict_memory_id: str = "") -> str:
        return self.repository.record_candidate(payload, user_id=user_id, source_fingerprint=source_fingerprint, dedupe_key=dedupe_key, extractor_version=extractor_version, conflict_memory_id=conflict_memory_id)

    def reject(self, candidate_id: str, reason: str, user_id: str = "local") -> dict:
        return self.repository.update_candidate(candidate_id, user_id=user_id, status="rejected", rejection_reason=reason)

    def publish(self, candidate_id: str, *, user_id: str = "local", confirmed: bool = False) -> str:
        candidate = self.require(candidate_id, user_id)
        if candidate["status"] not in {"candidate", "pending_confirmation"}:
            raise ValueError(f"Candidate cannot be published from {candidate['status']}")
        payload = candidate["payload"]
        explicit = bool(payload.get("explicit")) or confirmed
        sensitive = str(payload.get("privacy_level") or "normal") != "normal"
        if sensitive and not confirmed:
            self.repository.update_candidate(candidate_id, user_id=user_id, status="pending_confirmation")
            raise PermissionError("Sensitive memory requires explicit confirmation")
        conflict = str(candidate.get("conflict_memory_id") or "")
        if conflict:
            record = self.manager.supersede(conflict, str(payload["content"]), user_id=user_id, reason="confirmed memory candidate")
        else:
            record = self.manager.add_scoped(content=str(payload["content"]), memory_type=str(payload["memory_type"]), user_id=user_id, explicit=explicit, importance=float(payload.get("importance", .5)), confidence=float(payload.get("confidence", .5)), tags=list(payload.get("tags") or []), entity_refs=list(payload.get("entity_refs") or []), source="conversation_explicit" if explicit else "conversation_inference", origin=str(payload.get("origin") or "explicit_user"), valid_until=str(payload.get("valid_until") or ""), metadata={"source_message_ids": list(payload.get("source_message_ids") or []), "candidate_id": candidate_id}, reason="memory candidate published", scope=str(payload.get("scope") or "assistant_only"), repository_id=str(payload.get("repository_id") or ""))
        self.repository.update_candidate(candidate_id, user_id=user_id, status="active" if record.status == MemoryStatus.ACTIVE.value else "pending_confirmation", memory_id=record.id)
        return record.id

    def confirm(self, candidate_id: str, user_id: str = "local") -> str:
        return self.publish(candidate_id, user_id=user_id, confirmed=True)

    def invalidate_for_memory(self, memory_id: str, user_id: str = "local", reason: str = "source memory changed") -> int:
        count = 0
        for candidate in self.repository.candidates(user_id=user_id, limit=500):
            if candidate.get("memory_id") == memory_id and candidate["status"] not in {"rejected", "invalidated"}:
                self.repository.update_candidate(candidate["candidate_id"], user_id=user_id, status="invalidated", rejection_reason=reason); count += 1
        return count

    def require(self, candidate_id: str, user_id: str) -> dict:
        candidate = self.repository.candidate(candidate_id, user_id)
        if candidate is None: raise FileNotFoundError(f"Memory candidate not found: {candidate_id}")
        return candidate
