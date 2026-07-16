from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class MemoryMode(str, Enum):
    STRUCTURED_ONLY = "structured_only"
    LEGACY_AUDIT = "legacy_audit"
    LEGACY_MIGRATION = "legacy_migration"


@dataclass(frozen=True)
class MemoryRuntimeConfig:
    mode: MemoryMode = MemoryMode.STRUCTURED_ONLY

    @property
    def use_legacy_prompt(self) -> bool:
        return False

    @property
    def use_legacy_writes(self) -> bool:
        return False

    @property
    def audit_legacy(self) -> bool:
        return self.mode is MemoryMode.LEGACY_AUDIT

    @property
    def allow_legacy_migration(self) -> bool:
        return self.mode is MemoryMode.LEGACY_MIGRATION

    @classmethod
    def from_env(cls) -> "MemoryRuntimeConfig":
        raw = os.getenv("MEMORY_MODE", MemoryMode.STRUCTURED_ONLY.value).strip().lower()
        try:
            return cls(MemoryMode(raw))
        except ValueError:
            return cls()
