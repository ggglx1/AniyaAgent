import json
import time
from pathlib import Path
from threading import Lock


class AuditLog:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.log_dir = self.workdir / ".runtime" / "audit"
        self.lock = Lock()

    def write(self, run_id: str, event_type: str, payload: dict | None = None) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": time.time(),
            "run_id": run_id,
            "type": event_type,
            "payload": payload or {},
        }
        path = self.log_dir / f"{run_id}.jsonl"
        line = json.dumps(event, ensure_ascii=False, default=str)
        with self.lock:
            with path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")

