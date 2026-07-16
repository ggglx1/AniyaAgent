from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    MemoryHistoryRecord,
    MemoryRecord,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from .repository import MemoryRepository
from .workspace_sync import MemoryWorkspaceSync


class PersonalMemoryManager:
    def __init__(
        self,
        workdir: Path,
        repository: MemoryRepository | None = None,
        workspace_sync: MemoryWorkspaceSync | None = None,
    ):
        self.workdir = workdir.resolve()
        self.repository = repository or MemoryRepository(self.workdir)
        self.workspace_sync = workspace_sync or MemoryWorkspaceSync(self.workdir)

    def add(
        self,
        content: str,
        memory_type: str = MemoryType.NOTE.value,
        source: str = MemorySource.CONVERSATION.value,
        user_id: str = "local",
        explicit: bool = True,
        importance: float = 0.5,
        confidence: float = 1.0,
        tags: list[str] | None = None,
        entity_refs: list[str] | None = None,
        metadata: dict | None = None,
        valid_from: str = "",
        valid_until: str = "",
        privacy_level: str = "normal",
        retention_policy: str = "permanent",
        review_at: str = "",
        origin: str = "explicit_user",
        reason: str = "",
    ) -> MemoryRecord:
        self.validate_type(memory_type)
        self.validate_source(source)
        if not content.strip():
            raise ValueError("Memory content cannot be empty")
        now = self.now_iso()
        status = MemoryStatus.ACTIVE.value if explicit else MemoryStatus.PENDING_CONFIRMATION.value
        record = MemoryRecord(
            id=self.new_id("mem"),
            user_id=user_id,
            content=content.strip(),
            type=memory_type,
            status=status,
            source=source,
            importance=self.clamp(importance),
            confidence=self.clamp(confidence),
            created_at=now,
            updated_at=now,
            valid_from=valid_from,
            valid_until=valid_until,
            privacy_level=privacy_level,
            retention_policy=retention_policy,
            review_at=review_at,
            origin=origin,
            tags=tags or [],
            entity_refs=entity_refs or [],
            metadata=metadata or {},
        )
        history = self.history_record(record.id, "created", None, record.to_dict(), reason)
        created = self.repository.create(record, history)
        self.after_change("created", created)
        return created

    def confirm(self, memory_id: str, user_id: str = "local", reason: str = "user confirmed") -> MemoryRecord:
        before = self.require(memory_id, user_id)
        after = replace(
            before,
            status=MemoryStatus.ACTIVE.value,
            confidence=1.0,
            updated_at=self.now_iso(),
        )
        confirmed = self.repository.replace(
            before,
            after,
            self.history_record(memory_id, "confirmed", before.to_dict(), after.to_dict(), reason),
        )
        self.after_change("confirmed", confirmed)
        return confirmed

    def supersede(
        self,
        memory_id: str,
        content: str,
        user_id: str = "local",
        reason: str = "user corrected memory",
    ) -> MemoryRecord:
        old = self.require(memory_id, user_id)
        if not content.strip():
            raise ValueError("Memory content cannot be empty")
        now = self.now_iso()
        updated_old = replace(old, status=MemoryStatus.SUPERSEDED.value, updated_at=now)
        new = replace(
            old,
            id=self.new_id("mem"),
            content=content.strip(),
            status=MemoryStatus.ACTIVE.value,
            confidence=1.0,
            created_at=now,
            updated_at=now,
            last_accessed_at="",
            supersedes_memory_id=old.id,
        )
        created = self.repository.create_superseding(
            old,
            updated_old,
            new,
            self.history_record(old.id, "superseded", old.to_dict(), updated_old.to_dict(), reason),
            self.history_record(new.id, "created", None, new.to_dict(), reason),
        )
        self.after_change("superseded", created)
        return created

    def archive(self, memory_id: str, user_id: str = "local", reason: str = "archived") -> MemoryRecord:
        before = self.require(memory_id, user_id)
        after = replace(before, status=MemoryStatus.ARCHIVED.value, updated_at=self.now_iso())
        archived = self.repository.replace(
            before,
            after,
            self.history_record(memory_id, "archived", before.to_dict(), after.to_dict(), reason),
        )
        self.after_change("archived", archived)
        return archived

    def forget(self, memory_id: str, user_id: str = "local", reason: str = "user requested forget") -> MemoryRecord:
        before = self.require(memory_id, user_id)
        after = replace(
            before,
            content="[forgotten]",
            status=MemoryStatus.DELETED.value,
            tags=[],
            entity_refs=[],
            metadata={},
            updated_at=self.now_iso(),
        )
        before_audit = {
            "id": before.id,
            "user_id": before.user_id,
            "type": before.type,
            "status": before.status,
        }
        after_audit = {
            "id": after.id,
            "user_id": after.user_id,
            "type": after.type,
            "status": after.status,
        }
        forgotten = self.repository.forget(
            before,
            after,
            self.history_record(memory_id, "deleted", before_audit, after_audit, reason),
        )
        self.after_change("deleted", forgotten)
        return forgotten

    def search(self, query: str, user_id: str = "local", limit: int = 10) -> list[MemoryRecord]:
        return self.repository.search(user_id, query, limit)

    def add_batch(self, candidates: list[dict], user_id: str = "local") -> list[MemoryRecord]:
        """Controlled entry point used by the extraction pipeline."""
        created = []
        for item in candidates:
            created.append(self.add(user_id=user_id, **item))
        return created

    def list(
        self,
        user_id: str = "local",
        status: str = "",
        memory_type: str = "",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        statuses = [status] if status else None
        return self.repository.list(user_id, statuses=statuses, memory_type=memory_type, limit=limit)

    def history(self, memory_id: str, user_id: str = "local") -> list[MemoryHistoryRecord]:
        if self.repository.get(memory_id, user_id, include_deleted=True) is None:
            raise FileNotFoundError(f"Memory not found: {memory_id}")
        return self.repository.history(memory_id)

    def after_change(self, operation: str, record: MemoryRecord) -> None:
        active = self.repository.list(record.user_id, statuses=[MemoryStatus.ACTIVE.value], limit=500)
        self.workspace_sync.sync_memories(active)
        self.workspace_sync.append_daily(operation, record)

    def require(self, memory_id: str, user_id: str) -> MemoryRecord:
        record = self.repository.get(memory_id, user_id)
        if record is None:
            raise FileNotFoundError(f"Memory not found: {memory_id}")
        return record

    def validate_type(self, value: str) -> None:
        if value not in {item.value for item in MemoryType}:
            raise ValueError(f"Invalid memory type: {value}")

    def validate_source(self, value: str) -> None:
        if value not in {item.value for item in MemorySource}:
            raise ValueError(f"Invalid memory source: {value}")

    def history_record(
        self,
        memory_id: str,
        operation: str,
        before: dict | None,
        after: dict | None,
        reason: str,
    ) -> MemoryHistoryRecord:
        return MemoryHistoryRecord(
            id=self.new_id("mh"),
            memory_id=memory_id,
            operation=operation,
            before_snapshot=before,
            after_snapshot=after,
            reason=reason,
            created_at=self.now_iso(),
        )

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))
