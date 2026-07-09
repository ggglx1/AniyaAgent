from dataclasses import dataclass, field
from typing import Protocol

from .types import ChannelKind, TrustLevel


@dataclass
class ChannelMessage:
    channel_id: str
    user_id: str
    conversation_id: str
    text: str
    kind: ChannelKind = ChannelKind.WEB
    trust_level: TrustLevel = TrustLevel.MEDIUM
    files: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        return f"{self.channel_id}:{self.conversation_id}"


@dataclass
class AgentResponse:
    channel_id: str
    conversation_id: str
    run_id: str
    status: str
    text: str = ""
    error: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ChannelSendResult:
    ok: bool
    message: str = ""


class Channel(Protocol):
    channel_id: str
    kind: ChannelKind
    trust_level: TrustLevel

    def send(self, response: AgentResponse) -> ChannelSendResult:
        ...
