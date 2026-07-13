import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class ProfileStore:
    allowed_fields = {
        "display_name",
        "preferred_address",
        "language",
        "communication_style",
        "timezone",
        "work_hours",
        "quiet_hours",
        "reminder_preferences",
        "planning_preferences",
        "assistant_feedback",
        "proactive_paused",
    }

    def __init__(self, workdir: Path, user_id: str = "local", workspace_sync=None):
        self.workdir = workdir.resolve()
        self.user_id = user_id
        self.profile_dir = self.workdir / ".personal"
        self.profile_file = self.profile_dir / "profile.json"
        self.activity_file = self.profile_dir / "activity.jsonl"
        self.lock = threading.RLock()
        self.workspace_sync = workspace_sync
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def get(self) -> dict:
        with self.lock:
            if not self.profile_file.exists():
                return self.default_profile()
            try:
                data = json.loads(self.profile_file.read_text(encoding="utf-8"))
            except Exception:
                return self.default_profile()
            return {**self.default_profile(), **data, "user_id": self.user_id}

    def update(self, changes: dict, source: str = "user") -> dict:
        invalid = sorted(set(changes) - self.allowed_fields)
        if invalid:
            raise ValueError(f"Unsupported profile fields: {', '.join(invalid)}")
        with self.lock:
            before = self.get()
            after = {**before, **changes}
            after["user_id"] = self.user_id
            after["updated_at"] = self.now_iso()
            self.atomic_write(after)
            self.record_activity("profile.updated", before, after, source)
            if self.workspace_sync is not None:
                self.workspace_sync.sync_profile(after)
            return after

    def summary(self) -> str:
        profile = self.get()
        visible = []
        for key in (
            "preferred_address",
            "language",
            "communication_style",
            "timezone",
            "work_hours",
            "quiet_hours",
            "reminder_preferences",
            "planning_preferences",
        ):
            value = profile.get(key)
            if value not in (None, "", [], {}):
                visible.append(f"- {key}: {json.dumps(value, ensure_ascii=False)}")
        return "\n".join(visible)

    def default_profile(self) -> dict:
        return {
            "user_id": self.user_id,
            "display_name": "",
            "preferred_address": "",
            "language": "zh-CN",
            "communication_style": "concise",
            "timezone": "Asia/Shanghai",
            "work_hours": {},
            "quiet_hours": {},
            "reminder_preferences": {},
            "planning_preferences": {},
            "assistant_feedback": [],
            "proactive_paused": False,
            "updated_at": "",
        }

    def atomic_write(self, data: dict) -> None:
        temp = self.profile_file.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.profile_file)

    def record_activity(self, event: str, before: dict, after: dict, source: str) -> None:
        record = {
            "event": event,
            "user_id": self.user_id,
            "source": source,
            "before": before,
            "after": after,
            "created_at": self.now_iso(),
        }
        with self.activity_file.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
