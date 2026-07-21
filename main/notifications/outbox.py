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
                    claimed_at TEXT NOT NULL DEFAULT '', delivered_at TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    worker_id TEXT NOT NULL DEFAULT '', claim_token TEXT NOT NULL DEFAULT '', lease_expires_at TEXT NOT NULL DEFAULT '', sending_at TEXT NOT NULL DEFAULT '', business_reconciled_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_claim ON notification_outbox(state, available_at);
                CREATE TABLE IF NOT EXISTS channel_binding_codes (
                    code TEXT PRIMARY KEY, owner_id TEXT NOT NULL, channel_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL, created_at TEXT NOT NULL, consumed_at TEXT NOT NULL DEFAULT ''
                );
            """)
            for column, definition in {
                'worker_id': "TEXT NOT NULL DEFAULT ''", 'claim_token': "TEXT NOT NULL DEFAULT ''", 'lease_expires_at': "TEXT NOT NULL DEFAULT ''", 'sending_at': "TEXT NOT NULL DEFAULT ''", 'business_reconciled_at': "TEXT NOT NULL DEFAULT ''",
            }.items():
                if column not in {item['name'] for item in connection.execute('PRAGMA table_info(notification_outbox)')}:
                    connection.execute(f'ALTER TABLE notification_outbox ADD COLUMN {column} {definition}')

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

    def issue_binding_code(self, owner_id: str = "local", channel_id: str = "weixin", ttl_seconds: int = 600) -> str:
        code = uuid.uuid4().hex[:8].upper(); now = datetime.now(timezone.utc); expires = (now + timedelta(seconds=max(60, ttl_seconds))).isoformat().replace('+00:00','Z')
        with self.lock, self.connect() as connection:
            connection.execute("INSERT INTO channel_binding_codes(code,owner_id,channel_id,expires_at,created_at) VALUES (?,?,?,?,?)", (code,owner_id,channel_id,expires,now.isoformat().replace('+00:00','Z')))
        return code

    def confirm_binding_code(self, code: str, recipient_id: str, context_token: str) -> bool:
        now = self.now()
        with self.lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM channel_binding_codes WHERE code=? AND consumed_at='' AND expires_at>?", (code.strip().upper(),now)).fetchone()
            if not row:
                connection.execute("COMMIT"); return False
            connection.execute("UPDATE channel_binding_codes SET consumed_at=? WHERE code=? AND consumed_at=''", (now,row['code']))
            connection.execute("INSERT OR REPLACE INTO owner_channel_bindings(owner_id,channel_id,recipient_id,context_token,status,verified_at) VALUES (?,?,?,?, 'verified', ?)", (row['owner_id'],row['channel_id'],recipient_id,context_token,now))
            connection.execute("COMMIT")
        return True

    def binding(self, owner_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM owner_channel_bindings WHERE owner_id=? AND channel_id='weixin' AND status='verified'", (owner_id,)).fetchone()
        return dict(row) if row else None

    def invalidate_binding(self, owner_id: str) -> bool:
        with self.lock, self.connect() as connection:
            return bool(connection.execute(
                "UPDATE owner_channel_bindings SET status='invalid' WHERE owner_id=? AND channel_id='weixin' AND status='verified'",
                (owner_id,),
            ).rowcount)

    def enqueue(self, reminder_id: str, channel_id: str, recipient_id: str, payload: dict, occurrence: str) -> str:
        key = f"{reminder_id}:{channel_id}:{occurrence}"
        now = self.now()
        with self.lock, self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO notification_outbox(id, idempotency_key, reminder_id, channel_id, recipient_id, payload_json, state, available_at, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)", (f"out_{uuid.uuid4().hex[:16]}", key, reminder_id, channel_id, recipient_id, json.dumps(payload, ensure_ascii=False), now, now))
            row = connection.execute("SELECT id FROM notification_outbox WHERE idempotency_key=?", (key,)).fetchone()
        return row["id"]

    def claim(self, worker_id: str, limit: int = 20, lease_seconds: int = 120) -> list[dict]:
        now = self.now()
        claimed = []
        with self.lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE notification_outbox SET state='retry_scheduled', worker_id='', claim_token='', lease_expires_at='', error='claim lease expired before send' WHERE state='claimed' AND lease_expires_at<>'' AND lease_expires_at<?", (now,))
            connection.execute("UPDATE notification_outbox SET state='delivery_unknown', lease_expires_at='', error='sending lease expired; provider outcome is unknown' WHERE state='sending' AND lease_expires_at<>'' AND lease_expires_at<?", (now,))
            rows = connection.execute("SELECT * FROM notification_outbox WHERE state IN ('pending','retry_scheduled') AND available_at<=? ORDER BY created_at LIMIT ?", (now, limit)).fetchall()
            for row in rows:
                token = uuid.uuid4().hex
                lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat().replace('+00:00', 'Z')
                updated = connection.execute("UPDATE notification_outbox SET state='claimed', claimed_at=?, attempts=attempts+1, worker_id=?, claim_token=?, lease_expires_at=? WHERE id=? AND state IN ('pending','retry_scheduled')", (now, worker_id, token, lease, row["id"])).rowcount
                if updated:
                    item = dict(row); item.update({'worker_id': worker_id, 'claim_token': token, 'lease_expires_at': lease}); claimed.append(item)
            connection.execute("COMMIT")
        return claimed

    def begin_sending(self, outbox_id: str, claim_token: str) -> bool:
        with self.lock, self.connect() as connection:
            return bool(connection.execute("UPDATE notification_outbox SET state='sending',sending_at=? WHERE id=? AND state='claimed' AND claim_token=?", (self.now(), outbox_id, claim_token)).rowcount)

    def complete(self, outbox_id: str, ok: bool, error: str = "", claim_token: str = "") -> bool:
        now = self.now()
        with self.lock, self.connect() as connection:
            if ok:
                sql = "UPDATE notification_outbox SET state='delivered', delivered_at=?, error='', lease_expires_at='' WHERE id=?"
                args = [now, outbox_id]
                if claim_token: sql += " AND state='sending' AND claim_token=?"; args.append(claim_token)
                return bool(connection.execute(sql, args).rowcount)
            else:
                row = connection.execute("SELECT attempts FROM notification_outbox WHERE id=?", (outbox_id,)).fetchone()
                attempts = int(row["attempts"]) if row else 1
                state = 'failed' if attempts >= 5 else 'retry_scheduled'
                available = (datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * (2 ** min(attempts, 6))))).isoformat().replace('+00:00','Z')
                sql = "UPDATE notification_outbox SET state=?, available_at=?, error=?, lease_expires_at='' WHERE id=?"; args=[state, available, error[:1000], outbox_id]
                if claim_token: sql += " AND claim_token=?"; args.append(claim_token)
                return bool(connection.execute(sql, args).rowcount)

    def mark_business_reconciled(self, outbox_id: str) -> None:
        with self.lock, self.connect() as connection:
            connection.execute("UPDATE notification_outbox SET business_reconciled_at=? WHERE id=? AND state='delivered'", (self.now(), outbox_id))

    def unreconciled_deliveries(self) -> list[dict]:
        with self.connect() as connection:
            rows=connection.execute("SELECT * FROM notification_outbox WHERE state='delivered' AND business_reconciled_at='' ORDER BY delivered_at").fetchall()
        return [{**dict(row), 'payload': json.loads(row['payload_json'])} for row in rows]

    def unknown_deliveries(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM notification_outbox WHERE state='delivery_unknown' ORDER BY sending_at").fetchall()
        return [{**dict(row), 'payload': json.loads(row['payload_json'])} for row in rows]

    def reconcile_unknown(self, outbox_id: str, delivered: bool, note: str = "") -> bool:
        state = 'delivered' if delivered else 'retry_scheduled'; now = self.now()
        with self.lock, self.connect() as connection:
            return bool(connection.execute("UPDATE notification_outbox SET state=?, delivered_at=CASE WHEN ?='delivered' THEN ? ELSE delivered_at END, available_at=CASE WHEN ?='retry_scheduled' THEN ? ELSE available_at END, error=? WHERE id=? AND state='delivery_unknown'", (state,state,now,state,now,note[:1000],outbox_id)).rowcount)

    def list(self, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM notification_outbox ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
