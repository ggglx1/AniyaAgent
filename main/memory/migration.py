from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from pathlib import Path

from .manager import PersonalMemoryManager
from .legacy_audit import LegacyMemoryAudit


class LegacyMemoryMigration:
    """Preview-first importer. It never edits or deletes legacy Markdown files."""

    skip_words = ("protocol", "test", "tool", "procedure", "agent", "worktree", "session")

    def __init__(self, workdir: Path, manager: PersonalMemoryManager):
        self.workdir = workdir.resolve()
        self.manager = manager
        self.memory_dir = self.workdir / ".memory"
        self.audit = LegacyMemoryAudit(self.workdir)

    def preview(self) -> list[dict]:
        items = []
        for path in sorted(self.memory_dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            lowered = f"{path.name} {text[:400]}".lower()
            if any(word in lowered for word in self.skip_words):
                continue
            content = self.body(text)
            memory_type = self.memory_type(path.name, content)
            if not content or memory_type == "":
                continue
            items.append({"filename": path.name, "content": content[:1000], "memory_type": memory_type, "confidence": 0.7})
        return items

    def apply(self, selected_filenames: list[str], user_id: str = "local") -> list[str]:
        previews = {item["filename"]: item for item in self.preview()}
        created = []
        for filename in selected_filenames:
            item = previews.get(filename)
            if not item:
                continue
            record = self.manager.add(
                item["content"], memory_type=item["memory_type"], source="legacy_migration", user_id=user_id,
                explicit=False, confidence=item["confidence"], origin="migrated_legacy",
                metadata={"legacy_filename": filename}, reason="user-confirmed legacy migration",
            )
            created.append(record.id)
        return created

    def write_manifest(self) -> Path:
        """Write a review artifact only; it does not alter legacy Markdown or import data."""
        path = self.memory_dir / "legacy_migration_manifest.json"
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "audit": self.audit.report(), "items": self.preview()}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def snapshot_legacy_files(self) -> Path:
        return self.audit.snapshot()

    def body(self, text: str) -> str:
        text = re.sub(r"^---.*?---\s*", "", text, flags=re.DOTALL).strip()
        return re.sub(r"\s+", " ", text).strip()

    def memory_type(self, filename: str, body: str) -> str:
        text = f"{filename} {body}".lower()
        if "feedback" in text:
            return "user_feedback"
        if "goal" in text or "目标" in text:
            return "goal"
        if any(word in text for word in ("name", "language", "preference", "名字", "偏好", "喜欢")):
            return "preference"
        return "note"
