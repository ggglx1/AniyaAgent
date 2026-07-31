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


@dataclass
class ActionCandidate:
    """A non-executable interpretation of a user message."""
    candidate_id: str
    run_id: str
    source_message_id: str = ""
    intent: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    evidence_spans: list[str] = field(default_factory=list)
    recognizer: str = "rule-v2"
    language_confidence: str = "low"
    negated: bool = False
    hypothetical: bool = False
    quoted: bool = False
    capability_question: bool = False
    ambiguities: list[str] = field(default_factory=list)
    parse_version: str = "intent-v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionResolution:
    """The only intent-layer output consumed by the runtime router."""
    resolution_id: str
    candidate_id: str = ""
    status: str = "non_action"
    validated_action: StructuredCommand | None = None
    missing_fields: list[str] = field(default_factory=list)
    ambiguous_entities: list[str] = field(default_factory=list)
    temporal_issue: str = ""
    policy_reason: str = ""
    required_user_response: str = ""
    candidate: ActionCandidate | None = None
    execution_policy_version: str = "intent-policy-v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value
