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
                pending_action_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, track_id TEXT NOT NULL UNIQUE,
                command_json TEXT NOT NULL, missing_fields_json TEXT NOT NULL, confirmation_state TEXT NOT NULL,
                state TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS command_results (
                idempotency_key TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL
            )""")

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self, track_id: str, owner_id: str = "local") -> dict | None:
        now = self.now()
        with self.lock, self.connect() as connection:
            connection.execute("UPDATE pending_actions SET state='expired',updated_at=? WHERE state='pending' AND expires_at<=?", (now, now))
            row = connection.execute("SELECT * FROM pending_actions WHERE owner_id=? AND track_id=? AND state='pending'", (owner_id, track_id)).fetchone()
        return self.decode(row) if row else None

    def create(self, track_id: str, command: dict, missing_fields: list[str], *, owner_id: str = "local", confirmation_state: str = "missing_input", ttl_minutes: int = 30) -> dict:
        now = self.now(); expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat().replace("+00:00", "Z")
        action_id = f"pending_{uuid.uuid4().hex[:16]}"
        with self.lock, self.connect() as connection:
            connection.execute("UPDATE pending_actions SET state='superseded',updated_at=? WHERE owner_id=? AND track_id=? AND state='pending'", (now, owner_id, track_id))
            connection.execute("INSERT INTO pending_actions VALUES (?,?,?,?,?,?,?,?,?,?)", (action_id, owner_id, track_id, json.dumps(command, ensure_ascii=False), json.dumps(missing_fields, ensure_ascii=False), confirmation_state, "pending", expires, now, now))
        return self.get(track_id, owner_id) or {}

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
