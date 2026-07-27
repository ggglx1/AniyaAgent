from __future__ import annotations

import hashlib
from pathlib import Path


class CodingArtifactStore:
    def __init__(self, workdir: Path):
        self.root = workdir.resolve() / ".task_outputs" / "coding_artifacts"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, value: object, *, label: str = "tool_result") -> dict:
        text = str(value); digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        path = self.root / f"{label}_{digest[:16]}.txt"
        if not path.exists(): path.write_text(text, encoding="utf-8", errors="replace")
        return {"path": str(path), "sha256": digest, "chars": len(text)}
