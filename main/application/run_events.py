from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


TERMINAL = {"completed", "failed", "cancelled", "timed_out"}
ACTIVE = {"accepted", "queued", "running", "waiting_permission", "reconnecting"}
EVENT_STATUS = {
    "accepted": "accepted",
    "queued": "queued",
    "running": "running",
    "permission_request": "waiting_permission",
    "waiting_permission": "waiting_permission",
    "reconnecting": "reconnecting",
    "resumed": "running",
}


class RunEventStore:
    """SQLite-backed run state and replayable, monotonically ordered events."""

    def __init__(self, workdir: Path, ttl_seconds: int = 7 * 24 * 60 * 60, max_events: int = 2000):
        self.path = workdir.resolve() / ".runtime" / "runs.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(3600, ttl_seconds)
        self.max_events = max(100, max_events)
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self.initialize()
        self.recover_interrupted()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    request_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL DEFAULT 'local',
                    conversation_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_event_id INTEGER NOT NULL DEFAULT 0,
                    final_content TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_runs_status_updated
                    ON runs(status, updated_at);
                CREATE TABLE IF NOT EXISTS run_events (
                    request_id TEXT NOT NULL,
                    event_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(request_id, event_id),
                    FOREIGN KEY(request_id) REFERENCES runs(request_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_run_events_created
                    ON run_events(created_at);
                """
            )

            columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
            if "owner_id" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'local'")

    def create(self, run_id: str, conversation_id: str, track_id: str, owner_id: str = "local") -> dict:
        now = time.time()
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO runs(
                       request_id, owner_id, conversation_id, track_id, status,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 'accepted', ?, ?)""",
                (run_id, owner_id, conversation_id, track_id, now, now),
            )
            event = self._insert_event(
                connection,
                run_id,
                1,
                "accepted",
                {"conversation_id": conversation_id, "track_id": track_id},
                now,
            )
            connection.execute(
                "UPDATE runs SET last_event_id=1 WHERE request_id=?", (run_id,)
            )
            connection.execute("COMMIT")
            self._changed.notify_all()
        return event

    def publish(self, run_id: str, event_type: str, payload: dict | None = None) -> dict:
        if event_type in TERMINAL:
            values = payload or {}
            return self.finish(
                run_id,
                event_type,
                content=str(values.get("content") or ""),
                error_code=str(values.get("error_code") or ""),
                error_message=str(values.get("error") or values.get("error_message") or ""),
                metadata=dict(values.get("metadata") or {}),
                payload=values,
            )

        now = time.time()
        data = dict(payload or {})
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, last_event_id FROM runs WHERE request_id=?", (run_id,)
            ).fetchone()
            if row is None or row["status"] in TERMINAL:
                connection.execute("ROLLBACK")
                return {}
            event_id = int(row["last_event_id"]) + 1
            status = EVENT_STATUS.get(event_type, row["status"])
            event = self._insert_event(
                connection, run_id, event_id, event_type, data, now
            )
            connection.execute(
                "UPDATE runs SET status=?, last_event_id=?, updated_at=? WHERE request_id=?",
                (status, event_id, now, run_id),
            )
            self._trim_events(connection, run_id)
            connection.execute("COMMIT")
            self._changed.notify_all()
        return event

    def finish(
        self,
        run_id: str,
        status: str,
        *,
        content: str = "",
        error_code: str = "",
        error_message: str = "",
        metadata: dict | None = None,
        payload: dict | None = None,
    ) -> dict:
        if status not in TERMINAL:
            raise ValueError(f"Unsupported terminal run status: {status}")
        now = time.time()
        metadata_value = dict(metadata or {})
        data = {
            "status": status,
            "content": content,
            "error_code": error_code,
            "error": error_message,
            "metadata": metadata_value,
            **dict(payload or {}),
        }
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, last_event_id FROM runs WHERE request_id=?", (run_id,)
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                return {}
            if row["status"] in TERMINAL:
                connection.execute("ROLLBACK")
                events = self.replay(run_id, max(0, int(row["last_event_id"]) - 1))
                return events[-1] if events else {}
            event_id = int(row["last_event_id"]) + 1
            event = self._insert_event(connection, run_id, event_id, status, data, now)
            connection.execute(
                """UPDATE runs SET status=?, last_event_id=?, final_content=?,
                       error_code=?, error_message=?, metadata_json=?, updated_at=?,
                       finished_at=? WHERE request_id=?""",
                (
                    status,
                    event_id,
                    content,
                    error_code,
                    error_message,
                    json.dumps(metadata_value, ensure_ascii=False),
                    now,
                    now,
                    run_id,
                ),
            )
            self._trim_events(connection, run_id)
            connection.execute("COMMIT")
            self._changed.notify_all()
        return event

    def replay(self, run_id: str, after_sequence: int = 0, limit: int | None = None) -> list[dict]:
        sql = """SELECT request_id, event_id, event_type, payload_json, created_at
                 FROM run_events WHERE request_id=? AND event_id>?
                 ORDER BY event_id"""
        args: list[object] = [run_id, max(0, int(after_sequence))]
        if limit is not None:
            sql += " LIMIT ?"
            args.append(max(1, int(limit)))
        with self.connect() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self._event_record(row) for row in rows]

    def wait_for_events(self, run_id: str, after_sequence: int, timeout: float = 15) -> list[dict]:
        events = self.replay(run_id, after_sequence)
        if events:
            return events
        with self._changed:
            self._changed.wait(timeout=max(0.1, timeout))
        return self.replay(run_id, after_sequence)

    def state(self, run_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE request_id=?", (run_id,)
            ).fetchone()
        return self._state_record(row) if row else None

    def active(self, conversation_id: str = "") -> list[dict]:
        placeholders = ",".join("?" for _ in ACTIVE)
        args: list[object] = [*sorted(ACTIVE)]
        sql = f"SELECT * FROM runs WHERE status IN ({placeholders})"
        if conversation_id:
            sql += " AND conversation_id=?"
            args.append(conversation_id)
        sql += " ORDER BY created_at"
        with self.connect() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self._state_record(row) for row in rows]

    def cancel(self, run_id: str) -> bool:
        now = time.time()
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, last_event_id, cancel_requested FROM runs WHERE request_id=?",
                (run_id,),
            ).fetchone()
            if row is None or row["status"] in TERMINAL or row["cancel_requested"]:
                connection.execute("ROLLBACK")
                return False
            event_id = int(row["last_event_id"]) + 1
            self._insert_event(
                connection,
                run_id,
                event_id,
                "cancel_requested",
                {"reason": "user_requested"},
                now,
            )
            connection.execute(
                """UPDATE runs SET cancel_requested=1, last_event_id=?, updated_at=?
                   WHERE request_id=?""",
                (event_id, now, run_id),
            )
            connection.execute("COMMIT")
            self._changed.notify_all()
        return True

    def is_cancelled(self, run_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM runs WHERE request_id=?", (run_id,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def recover_interrupted(self) -> int:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT request_id FROM runs WHERE status NOT IN ('completed','failed','cancelled','timed_out')"
            ).fetchall()
        for row in rows:
            self.finish(
                row["request_id"],
                "failed",
                error_code="runtime_restarted",
                error_message="The runtime restarted before this run reached a terminal state.",
            )
        return len(rows)

    def cleanup(self) -> int:
        cutoff = time.time() - self.ttl_seconds
        with self._lock, self.connect() as connection:
            placeholders = ",".join("?" for _ in TERMINAL)
            result = connection.execute(
                f"DELETE FROM runs WHERE status IN ({placeholders}) AND updated_at<?",
                (*sorted(TERMINAL), cutoff),
            )
        return int(result.rowcount)

    def _insert_event(
        self,
        connection,
        run_id: str,
        event_id: int,
        event_type: str,
        payload: dict,
        created_at: float,
    ) -> dict:
        connection.execute(
            """INSERT INTO run_events(request_id,event_id,event_type,payload_json,created_at)
               VALUES (?,?,?,?,?)""",
            (
                run_id,
                event_id,
                event_type,
                json.dumps(payload, ensure_ascii=False, default=str),
                created_at,
            ),
        )
        return {
            "run_id": run_id,
            "event_id": event_id,
            "event_sequence": event_id,
            "type": event_type,
            "data": payload,
            **payload,
            "created_at": created_at,
        }

    def _trim_events(self, connection, run_id: str) -> None:
        connection.execute(
            """DELETE FROM run_events WHERE request_id=? AND event_id <= (
                   SELECT COALESCE(MAX(event_id), 0) - ? FROM run_events WHERE request_id=?
               )""",
            (run_id, self.max_events, run_id),
        )

    def _event_record(self, row) -> dict:
        payload = json.loads(row["payload_json"] or "{}")
        return {
            "run_id": row["request_id"],
            "event_id": int(row["event_id"]),
            "event_sequence": int(row["event_id"]),
            "type": row["event_type"],
            "data": payload,
            **payload,
            "created_at": row["created_at"],
        }

    def _state_record(self, row) -> dict:
        return {
            "request_id": row["request_id"],
            "run_id": row["request_id"],
            "owner_id": row["owner_id"],
            "conversation_id": row["conversation_id"],
            "track_id": row["track_id"],
            "status": row["status"],
            "event_id": int(row["last_event_id"]),
            "event_sequence": int(row["last_event_id"]),
            "final_content": row["final_content"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "cancel_requested": bool(row["cancel_requested"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "finished_at": row["finished_at"],
        }
