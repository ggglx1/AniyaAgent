from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


class NotificationOutbox:
    """Durable, atomically claimed notification delivery state machine."""

    def __init__(self, workdir: Path):
        self.path = workdir.resolve() / ".personal" / "notification_outbox.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS owner_channel_bindings (
                    owner_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, recipient_id TEXT NOT NULL,
                    context_token TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, verified_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, reminder_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL, recipient_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                    state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, available_at TEXT NOT NULL,
                    claimed_at TEXT NOT NULL DEFAULT '', delivered_at TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_claim ON notification_outbox(state, available_at);
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def bind_owner(self, owner_id: str, recipient_id: str, context_token: str) -> None:
        now = self.now()
        with self.lock, self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO owner_channel_bindings(owner_id, channel_id, recipient_id, context_token, status, verified_at) VALUES (?, 'weixin', ?, ?, 'verified', ?)", (owner_id, recipient_id, context_token, now))

    def binding(self, owner_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM owner_channel_bindings WHERE owner_id=? AND channel_id='weixin' AND status='verified'", (owner_id,)).fetchone()
        return dict(row) if row else None

    def enqueue(self, reminder_id: str, channel_id: str, recipient_id: str, payload: dict, occurrence: str) -> str:
        key = f"{reminder_id}:{channel_id}:{occurrence}"
        now = self.now()
        with self.lock, self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO notification_outbox(id, idempotency_key, reminder_id, channel_id, recipient_id, payload_json, state, available_at, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)", (f"out_{uuid.uuid4().hex[:16]}", key, reminder_id, channel_id, recipient_id, json.dumps(payload, ensure_ascii=False), now, now))
            row = connection.execute("SELECT id FROM notification_outbox WHERE idempotency_key=?", (key,)).fetchone()
        return row["id"]

    def claim(self, worker_id: str, limit: int = 20) -> list[dict]:
        now = self.now()
        claimed = []
        with self.lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT * FROM notification_outbox WHERE state IN ('pending','retry_scheduled') AND available_at<=? ORDER BY created_at LIMIT ?", (now, limit)).fetchall()
            for row in rows:
                updated = connection.execute("UPDATE notification_outbox SET state='claimed', claimed_at=?, attempts=attempts+1 WHERE id=? AND state IN ('pending','retry_scheduled')", (now, row["id"])).rowcount
                if updated:
                    claimed.append(dict(row))
            connection.execute("COMMIT")
        return claimed

    def complete(self, outbox_id: str, ok: bool, error: str = "") -> None:
        now = self.now()
        with self.lock, self.connect() as connection:
            if ok:
                connection.execute("UPDATE notification_outbox SET state='delivered', delivered_at=?, error='' WHERE id=?", (now, outbox_id))
            else:
                row = connection.execute("SELECT attempts FROM notification_outbox WHERE id=?", (outbox_id,)).fetchone()
                attempts = int(row["attempts"]) if row else 1
                state = 'failed' if attempts >= 5 else 'retry_scheduled'
                available = (datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * (2 ** min(attempts, 6))))).isoformat().replace('+00:00','Z')
                connection.execute("UPDATE notification_outbox SET state=?, available_at=?, error=? WHERE id=?", (state, available, error[:1000], outbox_id))

    def list(self, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM notification_outbox ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
