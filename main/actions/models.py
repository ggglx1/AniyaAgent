from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class StructuredCommand:
    command_id: str
    run_id: str
    source_message_id: str = ""
    action: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    risk_level: str = "read_only"
    confirmation_state: str = "not_required"
    execution_state: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
