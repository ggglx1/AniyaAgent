from collections import defaultdict
from typing import Callable

from .base import AgentResponse, ChannelSendResult
from .types import ChannelKind, TrustLevel


class MemoryChannel:
    def __init__(
        self,
        channel_id: str,
        kind: ChannelKind,
        trust_level: TrustLevel,
    ):
        self.channel_id = channel_id
        self.kind = kind
        self.trust_level = trust_level
        self.responses: dict[str, list[AgentResponse]] = defaultdict(list)

    def send(self, response: AgentResponse) -> ChannelSendResult:
        self.responses[response.conversation_id].append(response)
        return ChannelSendResult(True, "stored")

    def latest(self, conversation_id: str) -> AgentResponse | None:
        items = self.responses.get(conversation_id) or []
        return items[-1] if items else None


class CallbackChannel:
    def __init__(
        self,
        channel_id: str,
        kind: ChannelKind,
        trust_level: TrustLevel,
        sender: Callable[[AgentResponse], None],
    ):
        self.channel_id = channel_id
        self.kind = kind
        self.trust_level = trust_level
        self.sender = sender

    def send(self, response: AgentResponse) -> ChannelSendResult:
        self.sender(response)
        return ChannelSendResult(True, "sent")


class StdoutChannel:
    def __init__(
        self,
        channel_id: str = "cli",
        kind: ChannelKind = ChannelKind.CLI,
        trust_level: TrustLevel = TrustLevel.HIGH,
    ):
        self.channel_id = channel_id
        self.kind = kind
        self.trust_level = trust_level

    def send(self, response: AgentResponse) -> ChannelSendResult:
        if response.text:
            print(response.text)
        elif response.error:
            print(response.error)
        return ChannelSendResult(True, "printed")
