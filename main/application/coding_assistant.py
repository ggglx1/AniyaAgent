from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from main.tools.tools import Tools


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

    def handle(self, text: str, repository_root: str | Path, work_session_id: str = "") -> dict:
        """Independent Coding runtime with developer tools constrained to repository_root."""
        root = Path(repository_root).resolve(); repository_id = self.repository_id(root)
        session_id = work_session_id or self.new_work_session(root)["work_session_id"]
        track_id = f"coding:{repository_id}:{session_id}"
        self.repository.append_track_message("user", text, mode="coding", scope_id=repository_id, track_id=track_id, repository_id=repository_id, work_session_id=session_id)
        tools = Tools(root, capability_profile="developer")
        messages = [{"role": "user", "content": text}]
        tool_facts = []
        for _ in range(20):
            # The production composition lazily imports the shared LLM gateway. Coding-only
            # tools are constructed above, so Assistant never receives this capability set.
            from main.agent import main_loop
            response = main_loop.llm_gateway.messages.create(task_type="main", model=main_loop.MODEL, max_tokens=8000, system=("You are a coding agent. Work only inside the configured repository. " "Use tools to inspect, edit, and validate code. Never access paths outside the repository."), messages=messages, tools=tools.definitions)
            messages.append({"role": "assistant", "content": response.content})
            if getattr(response, "stop_reason", "") != "tool_use": break
            results = []
            for block in response.content:
                if getattr(block, "type", "") != "tool_use": continue
                output = tools.execute(block)
                result = {"type": "tool_result", "tool_use_id": block.id, "content": output}
                results.append(result); tool_facts.append(result)
            messages.append({"role": "user", "content": results})
        answer = "\n".join(getattr(block, "text", "") for message in messages if message.get("role") == "assistant" for block in (message.get("content") or []) if getattr(block, "type", "") == "text").strip()
        for result in tool_facts:
            self.repository.append_track_message("tool", result, mode="coding", scope_id=repository_id, track_id=track_id, repository_id=repository_id, work_session_id=session_id)
        self.repository.append_track_message("assistant", answer, mode="coding", scope_id=repository_id, track_id=track_id, repository_id=repository_id, work_session_id=session_id)
        self.repository.request_maintenance("project_summary", {"repository_id": repository_id, "work_session_id": session_id})
        return {"repository_id": repository_id, "work_session_id": session_id, "track_id": track_id, "text": answer}
