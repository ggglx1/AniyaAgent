from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


class ProcessingLedger:
    def __init__(self, workdir: Path):
        self.path = workdir.resolve() / ".memory" / "processing_ledger.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS processed_sources (
                source_message_id TEXT NOT NULL, extractor_version TEXT NOT NULL,
                processed_at TEXT NOT NULL, PRIMARY KEY(source_message_id, extractor_version))""")

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
            return connection.execute("SELECT 1 FROM processed_sources WHERE source_message_id=? AND extractor_version=?", (source_message_id, version)).fetchone() is not None

    def mark(self, source_message_ids: list[str], version: str, processed_at: str) -> None:
        with self.connect() as connection:
            connection.executemany("INSERT OR IGNORE INTO processed_sources(source_message_id, extractor_version, processed_at) VALUES (?, ?, ?)", [(item, version, processed_at) for item in source_message_ids])
