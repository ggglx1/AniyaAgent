from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class McpServerConfig:
    id: str; name: str; transport: str; command_or_url: str; enabled: bool = True; trust_level: str = "read_only"; allowed_modes: list[str] = field(default_factory=lambda:["assistant"]); timeout_seconds: int = 30
    def to_dict(self): return asdict(self)


@dataclass
class McpCapability:
    server_id: str; name: str; description: str; input_schema: dict; risk_level: str = "read_only"
    def to_dict(self): return asdict(self)
