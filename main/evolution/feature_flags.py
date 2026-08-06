from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_FLAGS = {
    "memory.auto_extract_long_term": True,
    "memory.daily_curator": True,
    "evolution.problem_discovery": False,
    "evolution.evolution_analyst": False,
    "evolution.prompt_candidate_generator": False,
    "assistant.planning_advisor": False,
    "attachments.attachment_analyst": False,
    "evolution.auto_canary": False,
    "memory.cross_mode_sharing": False,
}

class FeatureFlags:
    def __init__(self, workdir: Path):
        self.path = workdir.resolve() / ".runtime" / "evolution.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS feature_flags (name TEXT PRIMARY KEY, enabled INTEGER NOT NULL, updated_at TEXT NOT NULL, actor TEXT NOT NULL)")
            for name, enabled in DEFAULT_FLAGS.items():
                db.execute("INSERT OR IGNORE INTO feature_flags VALUES (?,?,?,?)", (name, int(enabled), self.now(), "system"))
            db.execute("CREATE TABLE IF NOT EXISTS feature_flag_events (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, old_value INTEGER NOT NULL, new_value INTEGER NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL)")

    def enabled(self, name: str) -> bool:
        if name not in DEFAULT_FLAGS: raise ValueError(f"Unknown feature flag: {name}")
        with sqlite3.connect(self.path) as db: row = db.execute("SELECT enabled FROM feature_flags WHERE name=?", (name,)).fetchone()
        return bool(row[0]) if row else DEFAULT_FLAGS[name]

    def set(self, name: str, enabled: bool, actor: str = "local") -> bool:
        if name not in DEFAULT_FLAGS: raise ValueError(f"Unknown feature flag: {name}")
        old = self.enabled(name); now = self.now()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE feature_flags SET enabled=?,updated_at=?,actor=? WHERE name=?", (int(enabled), now, actor, name))
            db.execute("INSERT INTO feature_flag_events(name,old_value,new_value,actor,created_at) VALUES (?,?,?,?,?)", (name, int(old), int(enabled), actor, now))
        return bool(enabled)

    def all(self) -> dict[str, bool]: return {name: self.enabled(name) for name in DEFAULT_FLAGS}
    def now(self): return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
