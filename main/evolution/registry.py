from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VALID = {"registered", "evaluated", "approved", "shadow", "canary", "active", "paused", "rolled_back", "retired", "stale", "invalidated"}
TRANSITIONS = {
    "registered": {"evaluated", "invalidated"}, "evaluated": {"approved", "registered", "invalidated"},
    "approved": {"shadow", "invalidated"}, "shadow": {"canary", "paused", "rolled_back"},
    "canary": {"active", "paused", "rolled_back"}, "active": {"paused", "rolled_back", "retired", "stale"},
    "paused": {"shadow", "canary", "rolled_back", "retired"}, "rolled_back": {"shadow", "retired"},
    "retired": set(), "stale": {"registered", "invalidated"}, "invalidated": set(),
}

@dataclass(frozen=True)
class EvolutionAsset:
    asset_id: str
    kind: str
    name: str
    version: str
    payload: dict
    status: str
    owner_id: str
    created_at: str
    updated_at: str

class EvolutionAssetRegistry:
    """Persistence-only control plane; it never writes user memory or production prompts."""
    def __init__(self, workdir: Path):
        self.path = workdir.resolve() / ".runtime" / "evolution.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS evolution_assets (asset_id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL, owner_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS evolution_events (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, operation TEXT NOT NULL, before_status TEXT, after_status TEXT, actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def create(self, kind: str, name: str, version: str, payload: dict, owner_id: str = "local") -> EvolutionAsset:
        now = self.now(); asset_id = f"asset_{uuid.uuid4().hex[:16]}"
        with self.connect() as db:
            db.execute("INSERT INTO evolution_assets VALUES (?,?,?,?,?,?,?,?,?)", (asset_id, kind, name, version, json.dumps(payload, ensure_ascii=False), "registered", owner_id, now, now))
            self.event(db, asset_id, "created", None, "registered", owner_id, "asset registered")
        return self.get(asset_id)

    def get(self, asset_id: str) -> EvolutionAsset:
        with self.connect() as db: row = db.execute("SELECT * FROM evolution_assets WHERE asset_id=?", (asset_id,)).fetchone()
        if not row: raise FileNotFoundError(f"Evolution asset not found: {asset_id}")
        return self.row(row)

    def list(self, status: str = "", limit: int = 100) -> list[EvolutionAsset]:
        query = "SELECT * FROM evolution_assets"; args: list = []
        if status: query += " WHERE status=?"; args.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"; args.append(max(1, min(limit, 500)))
        with self.connect() as db: rows = db.execute(query, args).fetchall()
        return [self.row(row) for row in rows]

    def transition(self, asset_id: str, status: str, actor: str = "local", reason: str = "") -> EvolutionAsset:
        if status not in VALID: raise ValueError(f"Invalid evolution status: {status}")
        current = self.get(asset_id)
        if status not in TRANSITIONS[current.status]: raise ValueError(f"Invalid evolution transition: {current.status} -> {status}")
        now = self.now()
        with self.connect() as db:
            db.execute("UPDATE evolution_assets SET status=?,updated_at=? WHERE asset_id=?", (status, now, asset_id))
            self.event(db, asset_id, "transition", current.status, status, actor, reason)
        return self.get(asset_id)

    def events(self, asset_id: str) -> list[dict]:
        with self.connect() as db: rows = db.execute("SELECT * FROM evolution_events WHERE asset_id=? ORDER BY created_at", (asset_id,)).fetchall()
        return [dict(row) for row in rows]

    def event(self, db, asset_id, operation, before, after, actor, reason):
        db.execute("INSERT INTO evolution_events VALUES (?,?,?,?,?,?,?,?)", (f"event_{uuid.uuid4().hex[:16]}", asset_id, operation, before, after, actor, reason, self.now()))

    def row(self, row):
        return EvolutionAsset(str(row["asset_id"]), str(row["kind"]), str(row["name"]), str(row["version"]), json.loads(row["payload_json"]), str(row["status"]), str(row["owner_id"]), str(row["created_at"]), str(row["updated_at"]))

    def now(self): return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
