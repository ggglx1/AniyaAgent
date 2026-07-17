from __future__ import annotations

from .repository import ConversationMemoryRepository
from main.storage.conversation_store import ConversationStore
from pathlib import Path
from datetime import datetime, timedelta, timezone


class ConversationRetentionService:
    """Privacy boundary for factual conversation data; sequence numbers are never rewritten."""

    def __init__(self, repository: ConversationMemoryRepository, personal_memory=None):
        self.repository = repository
        self.personal_memory = personal_memory

    def redact(self, message_id: str) -> dict:
        message = self.repository.message(message_id)
        original_text = self.text(message.content) if message else ""
        linked_memory_ids = self.repository.linked_long_term_memory_ids(message_id)
        self.repository.redact_message(message_id)
        self.repository.invalidate_message_sources(message_id)
        # A fact without its only raw source must not remain silently trusted.
        if self.personal_memory is not None:
            for memory_id in linked_memory_ids:
                try:
                    record = self.personal_memory.require(memory_id, "local")
                    if record.origin == "explicit_user" or self.repository.valid_source_count(memory_id) > 0:
                        continue
                    self.personal_memory.archive(memory_id, reason="all factual sources were redacted")
                except FileNotFoundError:
                    pass
        runtime_files = ConversationStore(self.repository.workdir).redact_text_everywhere(original_text)
        operational_files = self.redact_operational_artifacts(original_text)
        return {
            "message_id": message_id,
            "stores": {
                "fact_memory": "redacted",
                "runtime_artifacts": runtime_files,
                "operational_artifacts": operational_files,
                "linked_long_term_memories": linked_memory_ids,
            },
            "daily_memory_rebuild": True,
            "external_limitations": [
                "已发送的微信通知无法从微信服务端撤回",
                "模型服务商日志、系统备份和第三方副本不受本地删除控制",
            ],
        }

    def export(self) -> list[dict]:
        return self.repository.export()

    def text(self, content) -> str:
        return content if isinstance(content, str) else str(content)

    def redact_operational_artifacts(self, text: str) -> int:
        if not text:
            return 0
        changed = 0
        for directory in (".runtime/audit", ".transcripts", ".task_outputs"):
            root = self.repository.workdir / directory
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    raw = path.read_text(encoding="utf-8")
                    if text in raw:
                        path.write_text(raw.replace(text, "[redacted]"), encoding="utf-8")
                        changed += 1
                except (OSError, UnicodeDecodeError):
                    continue
        return changed

    def cleanup_expired_operational_artifacts(self, retention_days: int = 30) -> int:
        """Remove transient runtime artifacts only; factual and structured memory are retained."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
        removed = 0
        for directory in (".runtime/audit", ".runtime/conversations", ".transcripts", ".task_outputs"):
            root = self.repository.workdir / directory
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                    if modified < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed
