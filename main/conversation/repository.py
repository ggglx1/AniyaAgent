from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
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
            self.initialize_v2(connection)

    def initialize_v2(self, connection) -> None:
        """Versioned, additive migration for the three product conversation tracks.

        Legacy tables stay intact so a partially upgraded local database can always be
        opened again. New code uses the v2 tables and old personal data is copied once.
        """
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, details TEXT NOT NULL DEFAULT '')")
        # Operational tables may be introduced after the v2 data migration. They must
        # be ensured on every startup instead of being hidden behind the migration gate.
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS maintenance_requests (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'pending',
                worker_id TEXT NOT NULL DEFAULT '', claim_token TEXT NOT NULL DEFAULT '', lease_expires_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_maintenance_claim ON maintenance_requests(state, lease_expires_at, created_at);
            CREATE TABLE IF NOT EXISTS scheduler_lease (
                lease_name TEXT PRIMARY KEY, worker_id TEXT NOT NULL, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """)
        version = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
        if version >= 2:
            return
        backup = self.db_path.with_suffix(".pre_track_v2.db")
        if not backup.exists() and self.db_path.exists():
            # SQLite backup API includes WAL pages; copying only the .db file does not.
            target = sqlite3.connect(backup)
            try: connection.backup(target)
            finally: target.close()
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS conversation_tracks (
                owner_id TEXT NOT NULL, mode TEXT NOT NULL, scope_id TEXT NOT NULL, track_id TEXT NOT NULL,
                repository_id TEXT NOT NULL DEFAULT '', work_session_id TEXT NOT NULL DEFAULT '', topic_id TEXT NOT NULL DEFAULT '',
                retention_class TEXT NOT NULL DEFAULT 'long_term', expires_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(owner_id, mode, scope_id, track_id)
            );
            CREATE TABLE IF NOT EXISTS conversation_track_days (
                owner_id TEXT NOT NULL, mode TEXT NOT NULL, scope_id TEXT NOT NULL, track_id TEXT NOT NULL,
                local_date TEXT NOT NULL, timezone_at_creation TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(owner_id, mode, scope_id, track_id, local_date)
            );
            CREATE TABLE IF NOT EXISTS conversation_track_messages (
                message_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, mode TEXT NOT NULL, scope_id TEXT NOT NULL,
                track_id TEXT NOT NULL, repository_id TEXT NOT NULL DEFAULT '', work_session_id TEXT NOT NULL DEFAULT '',
                topic_id TEXT NOT NULL DEFAULT '', day_date TEXT NOT NULL, sequence INTEGER NOT NULL, track_sequence INTEGER NOT NULL,
                role TEXT NOT NULL, content_json TEXT NOT NULL, channel TEXT NOT NULL, timezone_at_write TEXT NOT NULL,
                created_at TEXT NOT NULL, reply_to_message_id TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}',
                retention_class TEXT NOT NULL DEFAULT 'long_term', expires_at TEXT NOT NULL DEFAULT '', redacted_at TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_track_messages_sequence ON conversation_track_messages(sequence);
            CREATE INDEX IF NOT EXISTS idx_track_messages_history ON conversation_track_messages(owner_id, mode, scope_id, track_id, track_sequence);
            CREATE INDEX IF NOT EXISTS idx_track_messages_expiry ON conversation_track_messages(expires_at);
            CREATE TABLE IF NOT EXISTS maintenance_requests (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'pending',
                worker_id TEXT NOT NULL DEFAULT '', claim_token TEXT NOT NULL DEFAULT '', lease_expires_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_maintenance_claim ON maintenance_requests(state, lease_expires_at, created_at);
            CREATE TABLE IF NOT EXISTS scheduler_lease (
                lease_name TEXT PRIMARY KEY, worker_id TEXT NOT NULL, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """)
        now = datetime.now().astimezone().isoformat()
        rows = connection.execute("SELECT * FROM conversation_messages ORDER BY seq").fetchall()
        for row in rows:
            connection.execute("INSERT OR IGNORE INTO conversation_tracks(owner_id, mode, scope_id, track_id, created_at, updated_at) VALUES ('local','assistant','personal','assistant:personal',?,?)", (now, now))
            connection.execute("INSERT OR IGNORE INTO conversation_track_days(owner_id,mode,scope_id,track_id,local_date,timezone_at_creation,created_at,updated_at) VALUES ('local','assistant','personal','assistant:personal',?,?,?,?)", (row['day_date'], row['timezone_at_write'], row['created_at'], now))
            connection.execute("""INSERT OR IGNORE INTO conversation_track_messages(message_id,owner_id,mode,scope_id,track_id,day_date,sequence,track_sequence,role,content_json,channel,timezone_at_write,created_at,reply_to_message_id,metadata_json,redacted_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (row['message_id'], 'local', 'assistant', 'personal', 'assistant:personal', row['day_date'], row['seq'], row['seq'], row['role'], row['content_json'], row['channel'], row['timezone_at_write'], row['created_at'], row['reply_to_message_id'], row['metadata_json'], row['redacted_at']))
        connection.execute("INSERT OR REPLACE INTO schema_migrations(version, applied_at, details) VALUES (2, ?, ?)", (now, 'three-track factual memory; legacy data copied to assistant:personal'))

    def append_track_message(self, role: str, content: object, *, mode: str = 'assistant', scope_id: str = 'personal', track_id: str = 'assistant:personal', owner_id: str = 'local', repository_id: str = '', work_session_id: str = '', topic_id: str = '', channel: str = 'web', timezone_name: str = 'Asia/Shanghai', reply_to_message_id: str = '', metadata: dict | None = None, retention_class: str = 'long_term', expires_at: str = '') -> ConversationMessage:
        if role not in {'user', 'assistant', 'tool', 'system'}:
            raise ValueError(f'Unsupported factual message role: {role}')
        now = datetime.now(ZoneInfo(timezone_name)); date = now.date().isoformat(); created_at = now.isoformat()
        with self.lock, self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO conversation_tracks(owner_id,mode,scope_id,track_id,repository_id,work_session_id,topic_id,retention_class,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (owner_id,mode,scope_id,track_id,repository_id,work_session_id,topic_id,retention_class,expires_at,created_at,created_at))
            connection.execute("UPDATE conversation_tracks SET updated_at=?, expires_at=CASE WHEN ?<>'' THEN ? ELSE expires_at END WHERE owner_id=? AND mode=? AND scope_id=? AND track_id=?", (created_at, expires_at, expires_at, owner_id,mode,scope_id,track_id))
            connection.execute("INSERT OR IGNORE INTO conversation_track_days(owner_id,mode,scope_id,track_id,local_date,timezone_at_creation,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (owner_id,mode,scope_id,track_id,date,timezone_name,created_at,created_at))
            sequence = int(connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM conversation_track_messages").fetchone()[0])
            track_sequence = int(connection.execute("SELECT COALESCE(MAX(track_sequence),0)+1 FROM conversation_track_messages WHERE owner_id=? AND mode=? AND scope_id=? AND track_id=?", (owner_id,mode,scope_id,track_id)).fetchone()[0])
            message = ConversationMessage(f'msg_{uuid.uuid4().hex[:16]}', date, sequence, role, self.normalize(content), channel, timezone_name, created_at, reply_to_message_id, metadata or {}, '', owner_id, mode, scope_id, track_id, repository_id, work_session_id, topic_id, retention_class, expires_at, track_sequence)
            connection.execute("""INSERT INTO conversation_track_messages(message_id,owner_id,mode,scope_id,track_id,repository_id,work_session_id,topic_id,day_date,sequence,track_sequence,role,content_json,channel,timezone_at_write,created_at,reply_to_message_id,metadata_json,retention_class,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (message.message_id,owner_id,mode,scope_id,track_id,repository_id,work_session_id,topic_id,date,sequence,track_sequence,role,json.dumps(message.content,ensure_ascii=False),channel,timezone_name,created_at,reply_to_message_id,json.dumps(message.metadata,ensure_ascii=False),retention_class,expires_at))
        return message

    def track_history(self, *, mode: str, scope_id: str, track_id: str, owner_id: str = 'local', limit: int = 50, before_sequence: int | None = None, include_redacted: bool = False) -> list[ConversationMessage]:
        conditions = ['owner_id=?','mode=?','scope_id=?','track_id=?']; args = [owner_id, mode, scope_id, track_id]
        if not include_redacted: conditions.append("redacted_at='' ")
        if before_sequence is not None: conditions.append('track_sequence<?'); args.append(before_sequence)
        args.append(max(1, min(limit, 500)))
        with self.connect() as connection:
            rows = connection.execute(f"SELECT * FROM conversation_track_messages WHERE {' AND '.join(conditions)} ORDER BY track_sequence DESC LIMIT ?", args).fetchall()
        return [self.to_track_message(row) for row in reversed(rows)]

    def search_track_messages(self, query: str, *, mode: str = "assistant", limit: int = 50, owner_id: str = "local") -> list[ConversationMessage]:
        if not query.strip(): return []
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM conversation_track_messages WHERE owner_id=? AND mode=? AND redacted_at='' AND content_json LIKE ? ORDER BY sequence DESC LIMIT ?", (owner_id, mode, f"%{query.strip()}%", max(1, min(limit, 200)))).fetchall()
        return [self.to_track_message(row) for row in reversed(rows)]

    def list_tracks(self, owner_id: str = 'local', mode: str = '') -> list[dict]:
        sql = 'SELECT * FROM conversation_tracks WHERE owner_id=?'; args = [owner_id]
        if mode: sql += ' AND mode=?'; args.append(mode)
        with self.connect() as connection: rows = connection.execute(sql + ' ORDER BY updated_at DESC', args).fetchall()
        return [dict(row) for row in rows]

    def expire_track_messages(self, now: str) -> int:
        with self.lock, self.connect() as connection:
            count = connection.execute("UPDATE conversation_track_messages SET content_json='{\"redacted\":true,\"reason\":\"retention_expired\"}', redacted_at=? WHERE expires_at<>'' AND expires_at<=? AND redacted_at=''", (now, now)).rowcount
            connection.execute("UPDATE conversation_tracks SET status='closed', updated_at=? WHERE mode='qa' AND NOT EXISTS (SELECT 1 FROM conversation_track_messages message WHERE message.track_id=conversation_tracks.track_id AND message.redacted_at='')", (now,))
            return count

    def request_maintenance(self, kind: str, payload: dict | None = None) -> str:
        now = datetime.now(timezone.utc).isoformat().replace('+00:00','Z'); request_id = f'maint_{uuid.uuid4().hex[:16]}'; normalized=json.dumps(payload or {},ensure_ascii=False,sort_keys=True)
        with self.lock, self.connect() as connection:
            existing=connection.execute("SELECT id FROM maintenance_requests WHERE kind=? AND payload_json=? AND state IN ('pending','claimed') ORDER BY created_at DESC LIMIT 1", (kind,normalized)).fetchone()
            if existing: return str(existing['id'])
            connection.execute("INSERT INTO maintenance_requests(id,kind,payload_json,created_at,updated_at) VALUES (?,?,?,?,?)", (request_id,kind,normalized,now,now))
        return request_id

    def acquire_scheduler_lease(self, worker_id: str, lease_seconds: int = 90) -> bool:
        now=datetime.now(timezone.utc); now_s=now.isoformat().replace('+00:00','Z'); expires=(now+__import__('datetime').timedelta(seconds=lease_seconds)).isoformat().replace('+00:00','Z')
        with self.lock, self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row=connection.execute("SELECT * FROM scheduler_lease WHERE lease_name='primary'").fetchone()
            allowed=row is None or row['worker_id']==worker_id or row['expires_at']<now_s
            if allowed: connection.execute("INSERT OR REPLACE INTO scheduler_lease(lease_name,worker_id,expires_at,updated_at) VALUES ('primary',?,?,?)", (worker_id,expires,now_s))
            connection.execute('COMMIT')
        return allowed

    def release_scheduler_lease(self, worker_id: str) -> None:
        with self.lock, self.connect() as connection:
            connection.execute("DELETE FROM scheduler_lease WHERE lease_name='primary' AND worker_id=?", (worker_id,))

    def claim_maintenance(self, worker_id: str, limit: int = 10, lease_seconds: int = 120) -> list[dict]:
        now = datetime.now(timezone.utc); now_s=now.isoformat().replace('+00:00','Z'); until=(now+__import__('datetime').timedelta(seconds=lease_seconds)).isoformat().replace('+00:00','Z'); claimed=[]
        with self.lock, self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("UPDATE maintenance_requests SET state='pending',worker_id='',claim_token='',lease_expires_at='' WHERE state='claimed' AND lease_expires_at<?", (now_s,))
            rows=connection.execute("SELECT * FROM maintenance_requests WHERE state='pending' ORDER BY created_at LIMIT ?", (limit,)).fetchall()
            for row in rows:
                token=uuid.uuid4().hex
                if connection.execute("UPDATE maintenance_requests SET state='claimed',worker_id=?,claim_token=?,lease_expires_at=?,updated_at=? WHERE id=? AND state='pending'", (worker_id,token,until,now_s,row['id'])).rowcount:
                    item=dict(row); item['claim_token']=token; claimed.append(item)
            connection.execute('COMMIT')
        return claimed

    def complete_maintenance(self, request_id: str, claim_token: str, error: str = '') -> bool:
        now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'); state='failed' if error else 'completed'
        with self.lock, self.connect() as connection:
            return bool(connection.execute("UPDATE maintenance_requests SET state=?,updated_at=?,completed_at=?,lease_expires_at='' WHERE id=? AND state='claimed' AND claim_token=?", (state,now,now,request_id,claim_token)).rowcount)

    def to_track_message(self, row) -> ConversationMessage:
        return ConversationMessage(row['message_id'],row['day_date'],row['sequence'],row['role'],json.loads(row['content_json']),row['channel'],row['timezone_at_write'],row['created_at'],row['reply_to_message_id'],json.loads(row['metadata_json']),row['redacted_at'],row['owner_id'],row['mode'],row['scope_id'],row['track_id'],row['repository_id'],row['work_session_id'],row['topic_id'],row['retention_class'],row['expires_at'],row['track_sequence'])

    def append_message(
        self, role: str, content: object, *, channel: str = "web", timezone_name: str = "Asia/Shanghai",
        reply_to_message_id: str = "", metadata: dict | None = None,
    ) -> ConversationMessage:
        # v2 track facts are canonical. Legacy tables are retained only for migration reads.
        return self.append_track_message(role, content, channel=channel, timezone_name=timezone_name,
                                         reply_to_message_id=reply_to_message_id, metadata=metadata)
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
        # Keep legacy callers and the new three-track archive in sync during migration.
        self.append_track_message(role, content, channel=channel, timezone_name=timezone_name,
                                  reply_to_message_id=reply_to_message_id, metadata=metadata)
        return message

    def recent_messages(self, limit: int = 12) -> list[ConversationMessage]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_track_messages WHERE mode='assistant' AND track_id='assistant:personal' AND redacted_at='' ORDER BY track_sequence DESC LIMIT ?", (max(1, limit),)
            ).fetchall()
        return [self.to_track_message(row) for row in reversed(rows)]

    def messages_for_day(self, local_date: str, include_redacted: bool = False) -> list[ConversationMessage]:
        with self.connect() as connection:
            sql = "SELECT * FROM conversation_track_messages WHERE mode='assistant' AND track_id='assistant:personal' AND day_date=?"
            if not include_redacted:
                sql += " AND redacted_at=''"
            rows = connection.execute(sql + " ORDER BY seq", (local_date,)).fetchall()
        return [self.to_track_message(row) for row in rows]

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
            connection.execute("UPDATE conversation_track_messages SET content_json=? WHERE message_id=?", (json.dumps(self.normalize(content), ensure_ascii=False), message_id))

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
            track_row = connection.execute(
                "SELECT day_date, mode, track_id FROM conversation_track_messages WHERE message_id=?",
                (message_id,),
            ).fetchone()
            if not row and not track_row:
                raise FileNotFoundError(f"Conversation message not found: {message_id}")
            if row:
                connection.execute("UPDATE conversation_messages SET content_json=?, redacted_at=? WHERE message_id=?", ('{"redacted": true}', now, message_id))
                connection.execute("UPDATE conversation_days SET daily_memory_status='needs_rebuild', updated_at=? WHERE local_date=?", (now, row["day_date"]))
            if track_row:
                connection.execute(
                    "UPDATE conversation_track_messages SET content_json=?, redacted_at=? WHERE message_id=?",
                    ('{"redacted": true}', now, message_id),
                )
                if track_row["mode"] == "assistant":
                    connection.execute(
                        "UPDATE conversation_days SET daily_memory_status='needs_rebuild', updated_at=? WHERE local_date=?",
                        (now, track_row["day_date"]),
                    )
                else:
                    connection.execute(
                        "INSERT INTO maintenance_requests(id,kind,payload_json,created_at,updated_at) VALUES (?,?,?,?,?)",
                        (
                            f"maint_{uuid.uuid4().hex[:16]}",
                            "conversation_redacted",
                            json.dumps({"message_id": message_id, "mode": track_row["mode"], "track_id": track_row["track_id"]}, ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
            connection.execute("DELETE FROM conversation_attachments WHERE message_id=?", (message_id,))

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
                   JOIN conversation_track_messages message ON message.message_id=source.message_id
                   WHERE source.memory_id=? AND message.redacted_at=''""", (memory_id,)
            ).fetchone()
        return int(row["count"])

    def message_is_active(self, message_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT 1 FROM conversation_track_messages WHERE message_id=? AND redacted_at=''", (message_id,)).fetchone()
        return row is not None

    def message(self, message_id: str):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM conversation_messages WHERE message_id=?", (message_id,)).fetchone()
            track_row = None if row else connection.execute(
                "SELECT * FROM conversation_track_messages WHERE message_id=?", (message_id,)
            ).fetchone()
        return self.to_message(row) if row else (self.to_track_message(track_row) if track_row else None)

    def export(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM conversation_track_messages ORDER BY sequence").fetchall()
        return [self.to_track_message(row).to_dict() for row in rows]

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
