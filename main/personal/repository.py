from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .models import PersonalProject, PersonalReminder, PersonalTask


class PersonalStateRepository:
    schema_version = 1

    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.state_dir = self.workdir / ".personal"
        self.db_path = self.state_dir / "personal_state.db"
        self.lock = threading.RLock()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
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
                CREATE TABLE IF NOT EXISTS personal_tasks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 3,
                    project_id TEXT NOT NULL DEFAULT '',
                    due_at TEXT NOT NULL DEFAULT '',
                    next_action TEXT NOT NULL DEFAULT '',
                    blockers_json TEXT NOT NULL DEFAULT '[]',
                    source_conversation TEXT NOT NULL DEFAULT '',
                    completion_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_personal_tasks_user_status
                    ON personal_tasks(user_id, status, due_at);
                CREATE TABLE IF NOT EXISTS personal_reminders (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    recurrence TEXT NOT NULL DEFAULT '',
                    target_channel TEXT NOT NULL DEFAULT 'web',
                    status TEXT NOT NULL,
                    task_id TEXT NOT NULL DEFAULT '',
                    project_id TEXT NOT NULL DEFAULT '',
                    person_ref TEXT NOT NULL DEFAULT '',
                    last_delivered_at TEXT NOT NULL DEFAULT '',
                    snoozed_until TEXT NOT NULL DEFAULT '',
                    delivery_result TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_personal_reminders_due
                    ON personal_reminders(user_id, status, scheduled_at);
                CREATE TABLE IF NOT EXISTS personal_projects (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    goal TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    next_action TEXT NOT NULL DEFAULT '',
                    blockers_json TEXT NOT NULL DEFAULT '[]',
                    key_decisions_json TEXT NOT NULL DEFAULT '[]',
                    review_cadence TEXT NOT NULL DEFAULT '',
                    last_reviewed_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_personal_projects_user_status
                    ON personal_projects(user_id, status);
                CREATE TABLE IF NOT EXISTS personal_activity (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    source TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_personal_activity_user_created
                    ON personal_activity(user_id, created_at DESC);
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(self.schema_version),),
            )

    def insert(self, table: str, record, activity: dict) -> None:
        data = self.encode_record(record.to_dict())
        columns = list(data)
        placeholders = ",".join("?" for _ in columns)
        with self.lock, self.connect() as connection:
            connection.execute(
                f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                [data[column] for column in columns],
            )
            self.insert_activity(connection, activity)

    def update(self, table: str, before, after, activity: dict) -> None:
        data = self.encode_record(after.to_dict())
        columns = [column for column in data if column not in {"id", "user_id", "created_at"}]
        assignments = ",".join(f"{column}=?" for column in columns)
        values = [data[column] for column in columns] + [before.id, before.user_id]
        with self.lock, self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE {table} SET {assignments} WHERE id=? AND user_id=?",
                values,
            )
            if cursor.rowcount != 1:
                raise FileNotFoundError(f"State record not found: {before.id}")
            self.insert_activity(connection, activity)

    def get(self, table: str, record_type, record_id: str, user_id: str):
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE id=? AND user_id=?",
                (record_id, user_id),
            ).fetchone()
        return self.decode_record(record_type, row) if row else None

    def list(self, table: str, record_type, user_id: str, statuses: list[str] | None, limit: int):
        clauses = ["user_id=?"]
        params: list = [user_id]
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        params.append(max(1, min(limit, 500)))
        order = "due_at" if table == "personal_tasks" else "scheduled_at" if table == "personal_reminders" else "updated_at"
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} "
                f"ORDER BY CASE WHEN {order}='' THEN 1 ELSE 0 END, {order}, updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self.decode_record(record_type, row) for row in rows]

    def due_reminders(self, user_id: str, before_at: str, limit: int = 50) -> list[PersonalReminder]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM personal_reminders
                WHERE user_id=? AND status IN ('scheduled', 'snoozed')
                  AND COALESCE(NULLIF(snoozed_until, ''), scheduled_at) <= ?
                ORDER BY COALESCE(NULLIF(snoozed_until, ''), scheduled_at)
                LIMIT ?
                """,
                (user_id, before_at, max(1, min(limit, 200))),
            ).fetchall()
        return [self.decode_record(PersonalReminder, row) for row in rows]

    def activity(self, user_id: str, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM personal_activity WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            before_json = item.pop("before_json", None)
            after_json = item.pop("after_json", None)
            item["before"] = json.loads(before_json) if before_json else None
            item["after"] = json.loads(after_json) if after_json else None
            result.append(item)
        return result

    def encode_record(self, data: dict) -> dict:
        encoded = dict(data)
        for field in ("blockers", "key_decisions"):
            if field in encoded:
                encoded[f"{field}_json"] = json.dumps(encoded.pop(field), ensure_ascii=False)
        return encoded

    def decode_record(self, record_type, row):
        data = dict(row)
        for field in ("blockers", "key_decisions"):
            json_field = f"{field}_json"
            if json_field in data:
                data[field] = json.loads(data.pop(json_field) or "[]")
        return record_type(**data)

    def insert_activity(self, connection, activity: dict) -> None:
        connection.execute(
            """
            INSERT INTO personal_activity(
                id, user_id, entity_type, entity_id, operation,
                before_json, after_json, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                activity["id"], activity["user_id"], activity["entity_type"],
                activity["entity_id"], activity["operation"],
                json.dumps(activity.get("before"), ensure_ascii=False) if activity.get("before") else None,
                json.dumps(activity.get("after"), ensure_ascii=False) if activity.get("after") else None,
                activity.get("source", "user"), activity["created_at"],
            ),
        )
