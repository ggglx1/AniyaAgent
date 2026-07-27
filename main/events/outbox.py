from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class DomainEventOutbox:
    def __init__(self, workdir: Path):
        self.path = workdir.resolve() / ".runtime" / "domain_events.db"; self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS domain_events (event_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,source_run_id TEXT NOT NULL,payload_json TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,completed_at TEXT NOT NULL DEFAULT '')")

    def connect(self): return sqlite3.connect(self.path, timeout=30)

    def publish(self, event_type: str, source_run_id: str, payload: dict) -> str:
        event_id = f"evt_{uuid.uuid4().hex[:16]}"; now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect() as connection: connection.execute("INSERT INTO domain_events(event_id,event_type,source_run_id,payload_json,created_at) VALUES (?,?,?,?,?)", (event_id,event_type,source_run_id,json.dumps(payload,ensure_ascii=False),now))
        return event_id
