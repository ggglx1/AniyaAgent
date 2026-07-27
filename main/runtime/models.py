from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


@dataclass
class RunRequest:
    run_id: str; user_id: str; channel_id: str; conversation_id: str; mode: str; track_id: str; text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteDecision:
    mode: str; run_type: str; intent: str; confidence: float; reason: str
    required_capabilities: list[str] = field(default_factory=list); requires_confirmation: bool = False; missing_fields: list[str] = field(default_factory=list); classifier_version: str = "rules-v1"
    def to_dict(self) -> dict: return asdict(self)


@dataclass
class UnifiedRunResult:
    run_id: str; status: str; output: str = ""; error: str = ""; error_code: str = ""; metadata: dict = field(default_factory=dict)
    def to_dict(self) -> dict: return asdict(self)
class RunStatus(StrEnum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
    RunStatus.TIMED_OUT.value,
}
