from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ConversationMessage:
    message_id: str
    day_date: str
    seq: int
    role: str
    content: object
    channel: str
    timezone_at_write: str
    created_at: str
    reply_to_message_id: str = ""
    metadata: dict = field(default_factory=dict)
    redacted_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
