from __future__ import annotations

import hashlib
import uuid
from pathlib import Path


class CodingAssistantService:
    """Repository-scoped coding boundary. Developer loop remains opt-in only."""

    def __init__(self, workdir: Path, repository, developer_runtime_factory):
        self.workdir = workdir.resolve(); self.repository = repository; self.developer_runtime_factory = developer_runtime_factory

    def repository_id(self, repository_root: str | Path) -> str:
        root = Path(repository_root).resolve()
        if root != self.workdir and self.workdir not in root.parents:
            raise ValueError("Coding repository must be inside the configured AniyaAgent workspace.")
        return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]

    def new_work_session(self, repository_root: str | Path) -> dict:
        repository_id = self.repository_id(repository_root); session_id = f"ws_{uuid.uuid4().hex[:12]}"
        return {"repository_id": repository_id, "work_session_id": session_id, "track_id": f"coding:{repository_id}:{session_id}"}

    def history(self, repository_id: str, work_session_id: str, limit: int = 50):
        return self.repository.track_history(mode="coding", scope_id=repository_id, track_id=f"coding:{repository_id}:{work_session_id}", limit=limit)
