from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import MemoryRecord, MemoryStatus


class MemoryWorkspaceSync:
    """Build user-readable Markdown views from structured personal state."""

    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.workspace_dir = self.workdir / "workspace"
        self.daily_dir = self.workspace_dir / "memory"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    def sync_profile(self, profile: dict) -> None:
        labels = {
            "display_name": "Display name",
            "preferred_address": "Preferred address",
            "language": "Language",
            "communication_style": "Communication style",
            "timezone": "Timezone",
            "work_hours": "Work hours",
            "quiet_hours": "Quiet hours",
            "reminder_preferences": "Reminder preferences",
            "planning_preferences": "Planning preferences",
        }
        lines = ["# User Profile", "", "Generated from approved structured profile state.", ""]
        for field, label in labels.items():
            value = profile.get(field)
            if value not in (None, "", [], {}):
                lines.append(f"- **{label}:** {value}")
        self.atomic_write(self.workspace_dir / "USER_PROFILE.md", "\n".join(lines).rstrip() + "\n")

    def sync_memories(self, records: list[MemoryRecord]) -> None:
        approved = [record for record in records if record.status == MemoryStatus.ACTIVE.value]
        approved.sort(key=lambda item: (item.importance, item.updated_at), reverse=True)
        lines = [
            "# Memory",
            "",
            "Generated from approved structured memories. Pending or deleted memories are excluded.",
            "",
        ]
        for record in approved[:100]:
            lines.append(f"- [{record.type}] {record.content} <!-- {record.id} -->")
        self.atomic_write(self.workspace_dir / "MEMORY.md", "\n".join(lines).rstrip() + "\n")

    def append_daily(self, operation: str, record: MemoryRecord) -> None:
        timestamp = datetime.now().astimezone()
        daily_file = self.daily_dir / f"{timestamp:%Y-%m-%d}.md"
        if not daily_file.exists():
            daily_file.write_text(f"# {timestamp:%Y-%m-%d}\n\n", encoding="utf-8")
        if record.status == MemoryStatus.DELETED.value:
            self.redact_daily(record.id)
        content = "[forgotten]" if record.status == MemoryStatus.DELETED.value else record.content
        with daily_file.open("a", encoding="utf-8") as file:
            file.write(
                f"- {timestamp:%H:%M:%S} `{operation}` [{record.type}] "
                f"{content} <!-- {record.id} -->\n"
            )

    def redact_daily(self, memory_id: str) -> None:
        marker = f"<!-- {memory_id} -->"
        for daily_file in self.daily_dir.glob("*.md"):
            lines = daily_file.read_text(encoding="utf-8").splitlines()
            changed = False
            for index, line in enumerate(lines):
                if marker in line and "[forgotten]" not in line:
                    prefix = line.split("`")[0]
                    lines[index] = f"{prefix}`deleted` [forgotten] {marker}"
                    changed = True
            if changed:
                self.atomic_write(daily_file, "\n".join(lines).rstrip() + "\n")

    def atomic_write(self, path: Path, content: str) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)
