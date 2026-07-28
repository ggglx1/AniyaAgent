from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


class DomainEventOutbox:
    """Durable event handoff with leases. Scheduler is the only consumer."""

    def __init__(self, workdir: Path):
        self.path = workdir.resolve() / ".runtime" / "domain_events.db"; self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS domain_events (
                event_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,source_run_id TEXT NOT NULL,payload_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',attempt_count INTEGER NOT NULL DEFAULT 0,next_attempt_at TEXT NOT NULL DEFAULT '',
                worker_id TEXT NOT NULL DEFAULT '',claim_token TEXT NOT NULL DEFAULT '',lease_expires_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,completed_at TEXT NOT NULL DEFAULT ''
            )""")
            existing = {row["name"] for row in connection.execute("PRAGMA table_info(domain_events)")}
            for name, definition in {"attempt_count":"INTEGER NOT NULL DEFAULT 0", "next_attempt_at":"TEXT NOT NULL DEFAULT ''", "worker_id":"TEXT NOT NULL DEFAULT ''", "claim_token":"TEXT NOT NULL DEFAULT ''", "lease_expires_at":"TEXT NOT NULL DEFAULT ''", "last_error":"TEXT NOT NULL DEFAULT ''", "completed_at":"TEXT NOT NULL DEFAULT ''"}.items():
                if name not in existing: connection.execute(f"ALTER TABLE domain_events ADD COLUMN {name} {definition}")

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None); connection.row_factory = sqlite3.Row; return connection

    def publish(self, event_type: str, source_run_id: str, payload: dict, *, event_id: str = "") -> str:
        identifier = event_id or f"evt_{uuid.uuid4().hex[:16]}"; now = self.now()
        with self.lock, self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO domain_events(event_id,event_type,source_run_id,payload_json,next_attempt_at,created_at) VALUES (?,?,?,?,?,?)", (identifier,event_type,source_run_id,json.dumps(payload,ensure_ascii=False),now,now))
        return identifier

    def claim(self, worker_id: str, limit: int = 20, lease_seconds: int = 120) -> list[dict]:
        now = self.now(); lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z"); claimed=[]
        with self.lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE domain_events SET state='pending',worker_id='',claim_token='',lease_expires_at='' WHERE state='claimed' AND lease_expires_at<?", (now,))
            rows = connection.execute("SELECT * FROM domain_events WHERE state IN ('pending','retry_scheduled') AND next_attempt_at<=? ORDER BY created_at LIMIT ?", (now, limit)).fetchall()
            for row in rows:
                token = uuid.uuid4().hex
                if connection.execute("UPDATE domain_events SET state='claimed',attempt_count=attempt_count+1,worker_id=?,claim_token=?,lease_expires_at=? WHERE event_id=? AND state IN ('pending','retry_scheduled')", (worker_id,token,lease,row["event_id"])).rowcount:
                    item=dict(row); item.update({"claim_token":token,"payload":json.loads(row["payload_json"] or "{}")}); claimed.append(item)
            connection.execute("COMMIT")
        return claimed

    def complete(self, event_id: str, claim_token: str) -> bool:
        with self.lock, self.connect() as connection:
            return bool(connection.execute("UPDATE domain_events SET state='completed',completed_at=?,lease_expires_at='' WHERE event_id=? AND state='claimed' AND claim_token=?", (self.now(),event_id,claim_token)).rowcount)

    def fail(self, event_id: str, claim_token: str, error: str, *, retryable: bool = True) -> bool:
        with self.lock, self.connect() as connection:
            row=connection.execute("SELECT attempt_count FROM domain_events WHERE event_id=?", (event_id,)).fetchone(); attempts=int(row["attempt_count"]) if row else 1
            state = "retry_scheduled" if retryable and attempts < 5 else "dead_letter"
            next_time = (datetime.now(timezone.utc)+timedelta(seconds=min(3600, 2 ** attempts))).isoformat().replace("+00:00","Z") if state == "retry_scheduled" else ""
            return bool(connection.execute("UPDATE domain_events SET state=?,next_attempt_at=?,last_error=?,lease_expires_at='' WHERE event_id=? AND state='claimed' AND claim_token=?", (state,next_time,error[:1000],event_id,claim_token)).rowcount)

    def stats(self) -> dict:
        with self.lock, self.connect() as connection:
            rows = connection.execute("SELECT state, COUNT(*) AS count FROM domain_events GROUP BY state").fetchall()
        values = {row["state"]: int(row["count"]) for row in rows}
        return {"pending": values.get("pending", 0), "retry_scheduled": values.get("retry_scheduled", 0), "claimed": values.get("claimed", 0), "completed": values.get("completed", 0), "dead_letter": values.get("dead_letter", 0)}

    def now(self) -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
