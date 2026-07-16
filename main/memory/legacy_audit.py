from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


class LegacyMemoryAudit:
    """Read-only inventory plus explicit snapshot support for old Markdown memory."""

    categories = ("user", "feedback", "project", "reference", "procedure")

    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.memory_dir = self.workdir / ".memory"

    def report(self) -> dict:
        items = []
        for path in sorted(self.memory_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            items.append({"filename": path.name, "category": self.category(path.name, text), "bytes": path.stat().st_size})
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "legacy_directory": str(self.memory_dir), "file_count": len(items),
            "estimated_prompt_tokens": sum(item["bytes"] for item in items) // 4,
            "items": items,
        }

    def snapshot(self) -> Path:
        destination = self.memory_dir / "legacy_readonly_backup" / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        destination.mkdir(parents=True, exist_ok=False)
        for source in self.memory_dir.glob("*.md"):
            shutil.copy2(source, destination / source.name)
        (destination / "manifest.json").write_text(json.dumps(self.report(), ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    def category(self, filename: str, text: str) -> str:
        value = f"{filename} {text[:500]}".lower()
        if "feedback" in value:
            return "feedback"
        if any(word in value for word in ("procedure", "protocol", "tool", "test")):
            return "procedure"
        if any(word in value for word in ("project", "repository", "workspace")):
            return "project"
        if any(word in value for word in ("user", "name", "preference", "language", "用户", "偏好")):
            return "user"
        return "reference"
