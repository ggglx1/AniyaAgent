from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


class PendingActionStore:
    """A single expiring foreground action per track, persisted independently of Web."""

    def __init__(self, workdir: Path):
        path = workdir.resolve() / ".runtime"
        path.mkdir(parents=True, exist_ok=True)
        self.path = path / "pending_actions.db"
        self.lock = threading.RLock()
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS pending_actions (
                pending_action_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, track_id TEXT NOT NULL,
                command_json TEXT NOT NULL, missing_fields_json TEXT NOT NULL, confirmation_state TEXT NOT NULL,
                state TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS command_results (
                idempotency_key TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
            connection.execute("CREATE TABLE IF NOT EXISTS pending_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            self.migrate(connection)

    def migrate(self, connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(pending_actions)")}
        # Early versions used a global UNIQUE(track_id). Rebuild once so two owners
        # cannot interfere and pending metadata survives restarts.
        indexes = list(connection.execute("PRAGMA index_list(pending_actions)"))
        # Only the old, global UNIQUE(track_id) needs a rebuild. The new partial
        # owner+track index must not be mistaken for it on every application start.
        unique_track = any(
            row["unique"] and {part["name"] for part in connection.execute(f"PRAGMA index_info({row['name']})")} == {"track_id"}
            for row in indexes
        )
        if unique_track:
            connection.executescript("""
                ALTER TABLE pending_actions RENAME TO pending_actions_legacy;
                CREATE TABLE pending_actions (
                    pending_action_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, track_id TEXT NOT NULL,
                    command_json TEXT NOT NULL, missing_fields_json TEXT NOT NULL, confirmation_state TEXT NOT NULL,
                    state TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    source_run_id TEXT NOT NULL DEFAULT '', source_message_id TEXT NOT NULL DEFAULT '', risk_level TEXT NOT NULL DEFAULT 'read_only'
                );
            """)
            legacy_columns = {row["name"] for row in connection.execute("PRAGMA table_info(pending_actions_legacy)")}
            target_columns = ["pending_action_id", "owner_id", "track_id", "command_json", "missing_fields_json", "confirmation_state", "state", "expires_at", "created_at", "updated_at", "source_run_id", "source_message_id", "risk_level"]
            select_columns = [name if name in legacy_columns else "''" for name in target_columns]
            connection.execute(f"INSERT INTO pending_actions({','.join(target_columns)}) SELECT {','.join(select_columns)} FROM pending_actions_legacy")
            connection.execute("DROP TABLE pending_actions_legacy")
            connection.execute("CREATE UNIQUE INDEX idx_pending_active_owner_track ON pending_actions(owner_id,track_id) WHERE state='pending'")
        else:
            for name, definition in {"source_run_id": "TEXT NOT NULL DEFAULT ''", "source_message_id": "TEXT NOT NULL DEFAULT ''", "risk_level": "TEXT NOT NULL DEFAULT 'read_only'"}.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE pending_actions ADD COLUMN {name} {definition}")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_active_owner_track ON pending_actions(owner_id,track_id) WHERE state='pending'")
        connection.execute("INSERT OR IGNORE INTO pending_schema_migrations(version,applied_at) VALUES (2,?)", (self.now(),))

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self, track_id: str, owner_id: str = "local") -> dict | None:
        self.expire()
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT * FROM pending_actions WHERE owner_id=? AND track_id=? AND state='pending'", (owner_id, track_id)).fetchone()
        return self.decode(row) if row else None

    def expire(self) -> list[dict]:
        now = self.now()
        with self.lock, self.connect() as connection:
            rows = connection.execute("SELECT * FROM pending_actions WHERE state='pending' AND expires_at<=?", (now,)).fetchall()
            connection.execute("UPDATE pending_actions SET state='expired',updated_at=? WHERE state='pending' AND expires_at<=?", (now, now))
        return [self.decode(row) for row in rows]

    def create(self, track_id: str, command: dict, missing_fields: list[str], *, owner_id: str = "local", confirmation_state: str = "missing_input", ttl_minutes: int = 30, source_run_id: str = "", source_message_id: str = "", risk_level: str = "read_only") -> dict:
        now = self.now(); expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat().replace("+00:00", "Z")
        action_id = f"pending_{uuid.uuid4().hex[:16]}"
        with self.lock, self.connect() as connection:
            connection.execute("UPDATE pending_actions SET state='superseded',updated_at=? WHERE owner_id=? AND track_id=? AND state='pending'", (now, owner_id, track_id))
            connection.execute("INSERT INTO pending_actions(pending_action_id,owner_id,track_id,command_json,missing_fields_json,confirmation_state,state,expires_at,created_at,updated_at,source_run_id,source_message_id,risk_level) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (action_id, owner_id, track_id, json.dumps(command, ensure_ascii=False), json.dumps(missing_fields, ensure_ascii=False), confirmation_state, "pending", expires, now, now, source_run_id, source_message_id, risk_level))
        return self.get(track_id, owner_id) or {}

    def update(self, pending_action_id: str, command: dict, missing_fields: list[str], *, confirmation_state: str = "missing_input", ttl_minutes: int = 30) -> dict | None:
        """Keep one pending business action while the user fills fields in stages."""
        now = self.now(); expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat().replace("+00:00", "Z")
        with self.lock, self.connect() as connection:
            connection.execute(
                "UPDATE pending_actions SET command_json=?,missing_fields_json=?,confirmation_state=?,expires_at=?,updated_at=? WHERE pending_action_id=? AND state='pending'",
                (json.dumps(command, ensure_ascii=False), json.dumps(missing_fields, ensure_ascii=False), confirmation_state, expires, now, pending_action_id),
            )
            row = connection.execute("SELECT * FROM pending_actions WHERE pending_action_id=?", (pending_action_id,)).fetchone()
        return self.decode(row) if row else None

    def resolve(self, track_id: str, *, owner_id: str = "local", state: str = "completed") -> None:
        with self.lock, self.connect() as connection:
            connection.execute("UPDATE pending_actions SET state=?,updated_at=? WHERE owner_id=? AND track_id=? AND state='pending'", (state, self.now(), owner_id, track_id))

    def command_result(self, idempotency_key: str) -> dict | None:
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT result_json FROM command_results WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        return json.loads(row["result_json"]) if row else None

    def save_command_result(self, idempotency_key: str, result: dict) -> None:
        with self.lock, self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO command_results VALUES (?,?,?)", (idempotency_key, json.dumps(result, ensure_ascii=False), self.now()))

    def decode(self, row) -> dict:
        item = dict(row); item["command"] = json.loads(item.pop("command_json")); item["missing_fields"] = json.loads(item.pop("missing_fields_json")); return item

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
