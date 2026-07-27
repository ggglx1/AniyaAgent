from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Capability:
    id: str; provider: str; name: str; description: str; input_schema: dict[str, Any]
    risk_level: str = "read_only"; allowed_modes: tuple[str, ...] = ("assistant",); side_effect: bool = False; timeout_seconds: int = 30; availability: str = "available"
    def to_dict(self) -> dict: return asdict(self)


@dataclass
class CapabilityResult:
    status: str; data: Any = None; error: str = ""; artifact: dict | None = None; audit: dict = field(default_factory=dict)
