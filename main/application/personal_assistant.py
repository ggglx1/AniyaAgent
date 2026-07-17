from __future__ import annotations

from main.channel import ChannelMessage
from main.channel.types import ChannelKind, TrustLevel


class PersonalAssistantService:
    """Personal track adapter. It deliberately exposes no developer tool profile."""

    track_id = "assistant:personal"

    def __init__(self, runtime, conversation):
        self.runtime = runtime
        self.conversation = conversation

    def handle(self, text: str, *, channel_id: str = "web", metadata: dict | None = None, event_callback=None):
        message = ChannelMessage(channel_id=channel_id, user_id="local", conversation_id="personal", text=text,
                                 kind=ChannelKind.WEB, trust_level=TrustLevel.HIGH, metadata=metadata or {})
        return self.runtime.handle_message(message, event_callback=event_callback)

    def history(self, limit: int = 50, before_sequence: int | None = None):
        return self.conversation.repository.track_history(mode="assistant", scope_id="personal", track_id=self.track_id, limit=limit, before_sequence=before_sequence)
