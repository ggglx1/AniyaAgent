from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import ConversationMessage


class ConversationMemoryRepository:
    """Append-only factual Web conversation archive and daily summaries."""

    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.directory = self.workdir / ".conversation"
        self.db_path = self.directory / "conversation_memory.db"
        self.lock = threading.RLock()
        self.directory.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS conversation_days (
                    local_date TEXT PRIMARY KEY,
                    timezone_at_creation TEXT NOT NULL,
                    daily_memory_status TEXT NOT NULL DEFAULT 'open',
                    summary TEXT NOT NULL DEFAULT '',
                    open_loops_json TEXT NOT NULL DEFAULT '[]',
                    important_events_json TEXT NOT NULL DEFAULT '[]',
                    task_changes_json TEXT NOT NULL DEFAULT '[]',
                    emotional_signals_json TEXT NOT NULL DEFAULT '[]',
                    daily_memory_json TEXT NOT NULL DEFAULT '{}',
                    generated_at TEXT NOT NULL DEFAULT '',
                    input_fingerprint TEXT NOT NULL DEFAULT '',
                    daily_memory_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    day_date TEXT NOT NULL,
                    seq INTEGER NOT NULL UNIQUE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool', 'system')),
                    content_json TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'web',
                    timezone_at_write TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reply_to_message_id TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    redacted_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(day_date) REFERENCES conversation_days(local_date)
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_messages_day ON conversation_messages(day_date, seq);
                CREATE TABLE IF NOT EXISTS daily_memory_sources (
                    local_date TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    PRIMARY KEY(local_date, message_id)
                );
                CREATE TABLE IF NOT EXISTS long_term_memory_sources (
                    memory_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    PRIMARY KEY(memory_id, message_id, relation)
                );
                CREATE TABLE IF NOT EXISTS conversation_attachments (
                    attachment_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_attachments_message ON conversation_attachments(message_id);
                """
            )
            self.ensure_column(connection, "conversation_days", "important_events_json", "TEXT NOT NULL DEFAULT '[]'")
            self.ensure_column(connection, "conversation_days", "task_changes_json", "TEXT NOT NULL DEFAULT '[]'")
            self.ensure_column(connection, "conversation_days", "emotional_signals_json", "TEXT NOT NULL DEFAULT '[]'")
            self.ensure_column(connection, "conversation_days", "daily_memory_json", "TEXT NOT NULL DEFAULT '{}'")
            self.ensure_column(connection, "conversation_days", "generated_at", "TEXT NOT NULL DEFAULT ''")
            self.ensure_column(connection, "conversation_days", "input_fingerprint", "TEXT NOT NULL DEFAULT ''")
            self.ensure_column(connection, "conversation_days", "daily_memory_error", "TEXT NOT NULL DEFAULT ''")

    def append_message(
        self, role: str, content: object, *, channel: str = "web", timezone_name: str = "Asia/Shanghai",
        reply_to_message_id: str = "", metadata: dict | None = None,
    ) -> ConversationMessage:
        if role not in {"user", "assistant", "tool", "system"}:
            raise ValueError(f"Unsupported factual message role: {role}")
        now = datetime.now(ZoneInfo(timezone_name))
        local_date, created_at = now.date().isoformat(), now.isoformat()
        with self.lock, self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO conversation_days(local_date, timezone_at_creation, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (local_date, timezone_name, created_at, created_at),
            )
            seq = int(connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM conversation_messages").fetchone()[0])
            message = ConversationMessage(
                message_id=f"msg_{uuid.uuid4().hex[:16]}", day_date=local_date, seq=seq, role=role,
                content=self.normalize(content), channel=channel, timezone_at_write=timezone_name,
                created_at=created_at, reply_to_message_id=reply_to_message_id, metadata=metadata or {},
            )
            connection.execute(
                """INSERT INTO conversation_messages(message_id, day_date, seq, role, content_json, channel,
                   timezone_at_write, created_at, reply_to_message_id, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (message.message_id, message.day_date, message.seq, message.role,
                 json.dumps(message.content, ensure_ascii=False), channel, timezone_name, created_at,
                 reply_to_message_id, json.dumps(message.metadata, ensure_ascii=False)),
            )
            connection.execute("UPDATE conversation_days SET updated_at=? WHERE local_date=?", (created_at, local_date))
        return message

    def recent_messages(self, limit: int = 12) -> list[ConversationMessage]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_messages WHERE redacted_at='' ORDER BY seq DESC LIMIT ?", (max(1, limit),)
            ).fetchall()
        return [self.to_message(row) for row in reversed(rows)]

    def messages_for_day(self, local_date: str, include_redacted: bool = False) -> list[ConversationMessage]:
        with self.connect() as connection:
            sql = "SELECT * FROM conversation_messages WHERE day_date=?"
            if not include_redacted:
                sql += " AND redacted_at=''"
            rows = connection.execute(sql + " ORDER BY seq", (local_date,)).fetchall()
        return [self.to_message(row) for row in rows]

    def day(self, local_date: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM conversation_days WHERE local_date=?", (local_date,)).fetchone()
        return self.day_record(row) if row else None

    def latest_daily_memory(self) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_days WHERE summary<>'' AND daily_memory_status='generated' ORDER BY local_date DESC LIMIT 1"
            ).fetchone()
        return self.day_record(row) if row else None

    def upsert_daily_memory(self, local_date: str, daily: dict, source_ids: list[str], input_fingerprint: str = "") -> None:
        now = datetime.now().astimezone().isoformat()
        with self.lock, self.connect() as connection:
            existing = connection.execute("SELECT timezone_at_creation FROM conversation_days WHERE local_date=?", (local_date,)).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO conversation_days(local_date, timezone_at_creation, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (local_date, "Asia/Shanghai", now, now),
                )
            connection.execute(
                """UPDATE conversation_days SET daily_memory_status=?, summary=?, open_loops_json=?, important_events_json=?,
                   task_changes_json=?, emotional_signals_json=?, daily_memory_json=?, generated_at=?, input_fingerprint=?, daily_memory_error='', updated_at=? WHERE local_date=?""",
                (daily.get("status", "generated"), daily.get("summary", ""), json.dumps(daily.get("open_loops", []), ensure_ascii=False),
                 json.dumps(daily.get("important_events", []), ensure_ascii=False), json.dumps(daily.get("task_changes", []), ensure_ascii=False),
                 json.dumps(daily.get("emotional_signals", []), ensure_ascii=False), json.dumps(daily, ensure_ascii=False),
                 daily.get("generated_at", now), input_fingerprint, now, local_date),
            )
            connection.execute("DELETE FROM daily_memory_sources WHERE local_date=?", (local_date,))
            connection.executemany("INSERT OR IGNORE INTO daily_memory_sources(local_date, message_id) VALUES (?, ?)", [(local_date, item) for item in source_ids])

    def mark_daily_failed(self, local_date: str, error: str) -> None:
        now = datetime.now().astimezone().isoformat()
        with self.lock, self.connect() as connection:
            connection.execute("UPDATE conversation_days SET daily_memory_status='failed', daily_memory_error=?, updated_at=? WHERE local_date=?", (error[:1000], now, local_date))

    def mark_daily_needs_rebuild(self, local_date: str) -> None:
        with self.lock, self.connect() as connection:
            connection.execute("UPDATE conversation_days SET daily_memory_status='needs_rebuild', updated_at=? WHERE local_date=?", (datetime.now().astimezone().isoformat(), local_date))

    def daily_sources(self, local_date: str) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT message_id FROM daily_memory_sources WHERE local_date=? ORDER BY message_id", (local_date,)).fetchall()
        return [row["message_id"] for row in rows]

    def list_days(self, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM conversation_days ORDER BY local_date DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [self.day_record(row) for row in rows]

    def store_attachment(self, message_id: str, content: object) -> str:
        attachment_id = f"att_{uuid.uuid4().hex[:16]}"
        now = datetime.now().astimezone().isoformat()
        with self.lock, self.connect() as connection:
            connection.execute("INSERT INTO conversation_attachments(attachment_id, message_id, content_json, created_at) VALUES (?, ?, ?, ?)", (attachment_id, message_id, json.dumps(self.normalize(content), ensure_ascii=False), now))
        return attachment_id

    def replace_message_content(self, message_id: str, content: object) -> None:
        with self.lock, self.connect() as connection:
            connection.execute("UPDATE conversation_messages SET content_json=? WHERE message_id=?", (json.dumps(self.normalize(content), ensure_ascii=False), message_id))

    def attachments(self, message_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM conversation_attachments WHERE message_id=? ORDER BY created_at", (message_id,)).fetchall()
        return [{**dict(row), "content": json.loads(row["content_json"])} for row in rows]

    def link_long_term_memory(self, memory_id: str, message_ids: list[str], relation: str = "explicit_source") -> None:
        with self.lock, self.connect() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO long_term_memory_sources(memory_id, message_id, relation) VALUES (?, ?, ?)",
                [(memory_id, item, relation) for item in message_ids],
            )

    def redact_message(self, message_id: str) -> None:
        now = datetime.now().astimezone().isoformat()
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT day_date FROM conversation_messages WHERE message_id=?", (message_id,)).fetchone()
            if not row:
                raise FileNotFoundError(f"Conversation message not found: {message_id}")
            connection.execute("UPDATE conversation_messages SET content_json=?, redacted_at=? WHERE message_id=?", ('{"redacted": true}', now, message_id))
            connection.execute("UPDATE conversation_days SET daily_memory_status='needs_rebuild', updated_at=? WHERE local_date=?", (now, row["day_date"]))

    def linked_long_term_memory_ids(self, message_id: str) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT memory_id FROM long_term_memory_sources WHERE message_id=?", (message_id,)
            ).fetchall()
        return [row["memory_id"] for row in rows]

    def invalidate_message_sources(self, message_id: str) -> None:
        with self.lock, self.connect() as connection:
            connection.execute("DELETE FROM daily_memory_sources WHERE message_id=?", (message_id,))
            connection.execute("DELETE FROM long_term_memory_sources WHERE message_id=?", (message_id,))

    def valid_source_count(self, memory_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count FROM long_term_memory_sources source
                   JOIN conversation_messages message ON message.message_id=source.message_id
                   WHERE source.memory_id=? AND message.redacted_at=''""", (memory_id,)
            ).fetchone()
        return int(row["count"])

    def message_is_active(self, message_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT 1 FROM conversation_messages WHERE message_id=? AND redacted_at=''", (message_id,)).fetchone()
        return row is not None

    def export(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM conversation_messages ORDER BY seq").fetchall()
        return [self.to_message(row).to_dict() for row in rows]

    def to_message(self, row) -> ConversationMessage:
        return ConversationMessage(
            message_id=row["message_id"], day_date=row["day_date"], seq=row["seq"], role=row["role"],
            content=json.loads(row["content_json"]), channel=row["channel"], timezone_at_write=row["timezone_at_write"],
            created_at=row["created_at"], reply_to_message_id=row["reply_to_message_id"],
            metadata=json.loads(row["metadata_json"]), redacted_at=row["redacted_at"],
        )

    def day_record(self, row) -> dict:
        value = dict(row)
        value["open_loops"] = json.loads(value.pop("open_loops_json") or "[]")
        value["important_events"] = json.loads(value.pop("important_events_json") or "[]")
        value["task_changes"] = json.loads(value.pop("task_changes_json") or "[]")
        value["emotional_signals"] = json.loads(value.pop("emotional_signals_json") or "[]")
        value["daily_memory"] = json.loads(value.pop("daily_memory_json") or "{}")
        return value

    def normalize(self, value):
        if isinstance(value, dict):
            return {str(key): self.normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.normalize(item) for item in value]
        if hasattr(value, "__dict__"):
            return self.normalize(vars(value))
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)

    def ensure_column(self, connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
