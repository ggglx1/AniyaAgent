from __future__ import annotations

import json

from main.conversation.retention import ConversationRetentionService
from main.memory.migration import LegacyMemoryMigration


class MemoryTool:
    def json(self, value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)


class GetFactualConversationTool(MemoryTool):
    name = "get_factual_conversation"
    definition = {
        "name": name,
        "description": "Read recent factual Web conversation records. These records are the auditable source, not compressed prompt history.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    }

    def __init__(self, conversation): self.conversation = conversation
    def run(self, limit: int = 30) -> str:
        return self.json([item.to_dict() for item in self.conversation.repository.recent_messages(max(1, min(limit, 200)))])


class ExportFactualConversationTool(MemoryTool):
    name = "export_factual_conversation"
    definition = {
        "name": name,
        "description": "Export the full factual Web conversation archive for the user.",
        "input_schema": {"type": "object", "properties": {}},
    }

    def __init__(self, retention): self.retention = retention
    def run(self) -> str: return self.json(self.retention.export())


class RedactFactualConversationTool(MemoryTool):
    name = "redact_factual_conversation_message"
    definition = {
        "name": name,
        "description": "Permanently redact one factual conversation message only when the user explicitly asks to delete it. Its derived daily context is invalidated.",
        "input_schema": {"type": "object", "properties": {"message_id": {"type": "string"}, "confirmed": {"type": "boolean"}}, "required": ["message_id", "confirmed"]},
    }

    def __init__(self, retention): self.retention = retention
    def run(self, message_id: str, confirmed: bool) -> str:
        if not confirmed:
            raise ValueError("Explicit user confirmation is required before redaction")
        self.retention.redact(message_id)
        return self.json({"redacted_message_id": message_id})


class PreviewLegacyMemoryMigrationTool(MemoryTool):
    name = "preview_legacy_memory_migration"
    definition = {
        "name": name,
        "description": "Preview eligible legacy Markdown memories for migration. This never imports or deletes files.",
        "input_schema": {"type": "object", "properties": {}},
    }

    def __init__(self, migration): self.migration = migration
    def run(self) -> str: return self.json(self.migration.preview())


class ApplyLegacyMemoryMigrationTool(MemoryTool):
    name = "apply_legacy_memory_migration"
    definition = {
        "name": name,
        "description": "Import specifically selected legacy Markdown entries only after the user confirms the preview. Original files stay unchanged.",
        "input_schema": {"type": "object", "properties": {"filenames": {"type": "array", "items": {"type": "string"}}, "confirmed": {"type": "boolean"}}, "required": ["filenames", "confirmed"]},
    }

    def __init__(self, migration): self.migration = migration
    def run(self, filenames: list[str], confirmed: bool) -> str:
        if not confirmed:
            raise ValueError("Explicit user confirmation is required before migration")
        return self.json({"imported_memory_ids": self.migration.apply(filenames)})


class WriteLegacyMigrationManifestTool(MemoryTool):
    name = "write_legacy_memory_migration_manifest"
    definition = {
        "name": name,
        "description": "Create an audit manifest for the legacy Markdown memory preview without changing legacy files.",
        "input_schema": {"type": "object", "properties": {}},
    }

    def __init__(self, migration): self.migration = migration
    def run(self) -> str: return self.json({"manifest": str(self.migration.write_manifest())})


class BackupLegacyMemoryTool(MemoryTool):
    name = "backup_legacy_memory"
    definition = {
        "name": name,
        "description": "Create a read-only timestamped copy of legacy Markdown memory before migration. It never deletes the original files.",
        "input_schema": {"type": "object", "properties": {"confirmed": {"type": "boolean"}}, "required": ["confirmed"]},
    }

    def __init__(self, migration): self.migration = migration
    def run(self, confirmed: bool) -> str:
        if not confirmed:
            raise ValueError("Explicit user confirmation is required before creating a backup")
        return self.json({"backup": str(self.migration.snapshot_legacy_files())})


def build_memory_tools(conversation, personal_memory, migration: LegacyMemoryMigration) -> list:
    retention = ConversationRetentionService(conversation.repository, personal_memory)
    return [
        GetFactualConversationTool(conversation), ExportFactualConversationTool(retention),
        RedactFactualConversationTool(retention), PreviewLegacyMemoryMigrationTool(migration),
        ApplyLegacyMemoryMigrationTool(migration), WriteLegacyMigrationManifestTool(migration),
        BackupLegacyMemoryTool(migration),
    ]
