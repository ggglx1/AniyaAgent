from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path


class ProcessingLedger:
    def __init__(self, workdir: Path):
        self.path = workdir.resolve() / ".memory" / "processing_ledger.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS processed_sources (
                source_message_id TEXT NOT NULL, extractor_version TEXT NOT NULL,
                candidate_kind TEXT NOT NULL DEFAULT 'all', state TEXT NOT NULL DEFAULT 'completed', processed_at TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '', PRIMARY KEY(source_message_id, extractor_version, candidate_kind))""")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(processed_sources)")}
            if "candidate_kind" not in columns: connection.execute("ALTER TABLE processed_sources ADD COLUMN candidate_kind TEXT NOT NULL DEFAULT 'all'")
            if "state" not in columns: connection.execute("ALTER TABLE processed_sources ADD COLUMN state TEXT NOT NULL DEFAULT 'completed'")
            if "error" not in columns: connection.execute("ALTER TABLE processed_sources ADD COLUMN error TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def processed(self, source_message_id: str, version: str) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1 FROM processed_sources WHERE source_message_id=? AND extractor_version=? AND state IN ('completed','permanent_rejected')", (source_message_id, version)).fetchone() is not None

    def mark(self, source_message_ids: list[str], version: str, processed_at: str) -> None:
        with self.connect() as connection:
            connection.executemany("INSERT OR REPLACE INTO processed_sources(source_message_id, extractor_version, candidate_kind, state, processed_at) VALUES (?, ?, 'all', 'completed', ?)", [(item, version, processed_at) for item in source_message_ids])

    def claim(self, source_message_id: str, version: str, candidate_kind: str = "all") -> bool:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            try:
                connection.execute("INSERT INTO processed_sources(source_message_id,extractor_version,candidate_kind,state,processed_at) VALUES (?, ?, ?, 'processing', ?)", (source_message_id,version,candidate_kind,now)); return True
            except sqlite3.IntegrityError: return False

    def fail(self, source_message_id: str, version: str, error: str, permanent: bool = False) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE processed_sources SET state=?, error=?, processed_at=? WHERE source_message_id=? AND extractor_version=?", ('permanent_rejected' if permanent else 'retryable_failed', error[:1000], datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), source_message_id, version))
