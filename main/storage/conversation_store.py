import json
import os
import time
import uuid
from pathlib import Path
from threading import Lock


class ConversationStore:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.store_dir = self.workdir / ".runtime" / "conversations"
        self.lock = Lock()

    def load(self, session_id: str) -> list:
        path = self.session_path(session_id)
        if not path.exists():
            return []
        with self.lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def save(self, session_id: str, messages: list) -> None:
        path = self.session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.atomic_write(path, messages)
            self.atomic_write(self.last_known_good_path(session_id), messages)

    def save_working(self, session_id: str, messages: list) -> None:
        with self.lock:
            self.atomic_write(self.working_path(session_id), messages)

    def load_last_known_good(self, session_id: str) -> list:
        path = self.last_known_good_path(session_id)
        if not path.exists(): return []
        with self.lock: return json.loads(path.read_text(encoding="utf-8"))

    def quarantine(self, session_id: str, run_id: str, messages: list, reason: str, diagnostics: list[str] | None = None) -> Path:
        path = self.store_dir / self.safe_id(session_id) / "quarantine" / f"{run_id}_{int(time.time())}.json"
        payload = {"reason": reason, "diagnostics": diagnostics or [], "messages": messages}
        with self.lock:
            self.atomic_write(path, payload)
        return path

    def atomic_write(self, path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(temp, path)

    def checkpoint(self, session_id: str, run_id: str, messages: list) -> Path:
        path = self.store_dir / self.safe_id(session_id) / "checkpoints" / f"{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            path.write_text(
                json.dumps(messages, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        return path

    def session_path(self, session_id: str) -> Path:
        return self.store_dir / self.safe_id(session_id) / "messages.json"

    def last_known_good_path(self, session_id: str) -> Path:
        return self.store_dir / self.safe_id(session_id) / "last_known_good.json"

    def working_path(self, session_id: str) -> Path:
        return self.store_dir / self.safe_id(session_id) / "working.json"

    def safe_id(self, value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value))

    def redact_text_everywhere(self, text: str) -> int:
        """Best-effort removal of a factual payload from transient runtime/checkpoint copies."""
        if not text:
            return 0
        changed = 0
        for path in self.store_dir.rglob("*.json") if self.store_dir.exists() else []:
            try:
                raw = path.read_text(encoding="utf-8")
                if text not in raw:
                    continue
                path.write_text(raw.replace(text, "[redacted]"), encoding="utf-8")
                changed += 1
            except OSError:
                continue
        return changed
