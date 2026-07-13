from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .models import MemoryHistoryRecord, MemoryRecord


class MemoryRepository:
    schema_version = 1

    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.memory_dir = self.workdir / ".memory"
        self.db_path = self.memory_dir / "personal_memory.db"
        self.lock = threading.RLock()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.lock, self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL DEFAULT '',
                    valid_from TEXT NOT NULL DEFAULT '',
                    valid_until TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    entity_refs_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    supersedes_memory_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_memories_user_status
                    ON memories(user_id, status);
                CREATE INDEX IF NOT EXISTS idx_memories_user_type
                    ON memories(user_id, type);
                CREATE TABLE IF NOT EXISTS memory_history (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_history_memory
                    ON memory_history(memory_id, created_at);
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(self.schema_version),),
            )

    def create(self, record: MemoryRecord, history: MemoryHistoryRecord) -> MemoryRecord:
        with self.lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO memories(
                    id, user_id, content, type, status, source, importance, confidence,
                    created_at, updated_at, last_accessed_at, valid_from, valid_until,
                    tags_json, entity_refs_json, metadata_json, supersedes_memory_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self.record_values(record),
            )
            self.insert_history(connection, history)
        return record

    def get(self, memory_id: str, user_id: str, include_deleted: bool = False) -> MemoryRecord | None:
        query = "SELECT * FROM memories WHERE id=? AND user_id=?"
        params = [memory_id, user_id]
        if not include_deleted:
            query += " AND status <> 'deleted'"
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return MemoryRecord.from_row(row) if row else None

    def list(
        self,
        user_id: str,
        statuses: list[str] | None = None,
        memory_type: str = "",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        clauses = ["user_id=?"]
        params: list = [user_id]
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        else:
            clauses.append("status <> 'deleted'")
        if memory_type:
            clauses.append("type=?")
            params.append(memory_type)
        params.append(max(1, min(limit, 500)))
        query = f"SELECT * FROM memories WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    def search(self, user_id: str, query: str, limit: int = 20) -> list[MemoryRecord]:
        terms = [term.lower() for term in query.split() if len(term.strip()) > 1]
        if not terms:
            return self.list(user_id, statuses=["active"], limit=limit)
        clauses = []
        params: list = [user_id]
        for term in terms[:8]:
            clauses.append("LOWER(content) LIKE ?")
            params.append(f"%{term}%")
        params.append(max(1, min(limit, 100)))
        sql = (
            "SELECT * FROM memories WHERE user_id=? AND status='active' AND ("
            + " OR ".join(clauses)
            + ") ORDER BY importance DESC, confidence DESC, updated_at DESC LIMIT ?"
        )
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    def replace(
        self,
        before: MemoryRecord,
        after: MemoryRecord,
        history: MemoryHistoryRecord,
    ) -> MemoryRecord:
        with self.lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE memories SET content=?, type=?, status=?, source=?, importance=?, confidence=?,
                    updated_at=?, last_accessed_at=?, valid_from=?, valid_until=?, tags_json=?,
                    entity_refs_json=?, metadata_json=?, supersedes_memory_id=?
                WHERE id=? AND user_id=?
                """,
                (
                    after.content,
                    after.type,
                    after.status,
                    after.source,
                    after.importance,
                    after.confidence,
                    after.updated_at,
                    after.last_accessed_at,
                    after.valid_from,
                    after.valid_until,
                    json.dumps(after.tags, ensure_ascii=False),
                    json.dumps(after.entity_refs, ensure_ascii=False),
                    json.dumps(after.metadata, ensure_ascii=False),
                    after.supersedes_memory_id,
                    before.id,
                    before.user_id,
                ),
            )
            self.insert_history(connection, history)
        return after

    def forget(
        self,
        before: MemoryRecord,
        after: MemoryRecord,
        history: MemoryHistoryRecord,
    ) -> MemoryRecord:
        redacted = json.dumps({"id": before.id, "redacted": True}, ensure_ascii=False)
        with self.lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE memories SET content=?, status=?, updated_at=?, tags_json='[]',
                    entity_refs_json='[]', metadata_json='{}'
                WHERE id=? AND user_id=?
                """,
                (after.content, after.status, after.updated_at, before.id, before.user_id),
            )
            connection.execute(
                "UPDATE memory_history SET before_json=?, after_json=? WHERE memory_id=?",
                (redacted, redacted, before.id),
            )
            self.insert_history(connection, history)
        return after

    def create_superseding(
        self,
        old: MemoryRecord,
        updated_old: MemoryRecord,
        new: MemoryRecord,
        old_history: MemoryHistoryRecord,
        new_history: MemoryHistoryRecord,
    ) -> MemoryRecord:
        with self.lock, self.connect() as connection:
            connection.execute(
                "UPDATE memories SET status=?, updated_at=? WHERE id=? AND user_id=?",
                (updated_old.status, updated_old.updated_at, old.id, old.user_id),
            )
            connection.execute(
                """
                INSERT INTO memories(
                    id, user_id, content, type, status, source, importance, confidence,
                    created_at, updated_at, last_accessed_at, valid_from, valid_until,
                    tags_json, entity_refs_json, metadata_json, supersedes_memory_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self.record_values(new),
            )
            self.insert_history(connection, old_history)
            self.insert_history(connection, new_history)
        return new

    def history(self, memory_id: str) -> list[MemoryHistoryRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_history WHERE memory_id=? ORDER BY created_at",
                (memory_id,),
            ).fetchall()
        return [
            MemoryHistoryRecord(
                id=row["id"],
                memory_id=row["memory_id"],
                operation=row["operation"],
                before_snapshot=json.loads(row["before_json"]) if row["before_json"] else None,
                after_snapshot=json.loads(row["after_json"]) if row["after_json"] else None,
                reason=row["reason"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def record_values(self, record: MemoryRecord) -> tuple:
        return (
            record.id,
            record.user_id,
            record.content,
            record.type,
            record.status,
            record.source,
            record.importance,
            record.confidence,
            record.created_at,
            record.updated_at,
            record.last_accessed_at,
            record.valid_from,
            record.valid_until,
            json.dumps(record.tags, ensure_ascii=False),
            json.dumps(record.entity_refs, ensure_ascii=False),
            json.dumps(record.metadata, ensure_ascii=False),
            record.supersedes_memory_id,
        )

    def insert_history(self, connection, history: MemoryHistoryRecord) -> None:
        connection.execute(
            """
            INSERT INTO memory_history(id, memory_id, operation, before_json, after_json, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                history.id,
                history.memory_id,
                history.operation,
                json.dumps(history.before_snapshot, ensure_ascii=False) if history.before_snapshot else None,
                json.dumps(history.after_snapshot, ensure_ascii=False) if history.after_snapshot else None,
                history.reason,
                history.created_at,
            ),
        )
