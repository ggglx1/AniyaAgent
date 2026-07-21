from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


class QaService:
    """Direct LLM Q&A path: no tools, skills, personal state, or default memory injection."""

    def __init__(self, llm_gateway, model: str, repository):
        self.llm_gateway = llm_gateway; self.model = model; self.repository = repository

    def new_topic(self) -> str:
        return f"topic_{uuid.uuid4().hex[:12]}"

    def active_topic(self) -> str:
        tracks = [track for track in self.repository.list_tracks(mode="qa") if track.get("status") == "active"]
        if not tracks: return self.new_topic()
        track_id = str(tracks[0]["track_id"])
        return track_id.split("qa:", 1)[-1]

    def ask(self, question: str, topic_id: str, *, context_limit: int = 8) -> str:
        track_id = f"qa:{topic_id}"; expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00','Z')
        self.repository.append_track_message("user", question, mode="qa", scope_id="knowledge", track_id=track_id, topic_id=topic_id, retention_class="qa_30_days", expires_at=expires)
        history = self.repository.track_history(mode="qa", scope_id="knowledge", track_id=track_id, limit=context_limit)
        messages = [{"role": item.role, "content": item.content} for item in history if item.role in {"user", "assistant"}]
        response = self.llm_gateway.messages.create(task_type="main", model=self.model, max_tokens=1024, system="Answer the knowledge question directly and concisely. Do not use tools or personal memory.", messages=messages)
        text = "\n".join(getattr(block, "text", "") for block in getattr(response, "content", []) if getattr(block, "type", "") == "text").strip()
        self.repository.append_track_message("assistant", text, mode="qa", scope_id="knowledge", track_id=track_id, topic_id=topic_id, retention_class="qa_30_days", expires_at=expires)
        return text
