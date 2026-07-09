import json
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
            path.write_text(
                json.dumps(messages, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

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

    def safe_id(self, value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value))

