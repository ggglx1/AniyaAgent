from .base import AgentResponse, Channel, ChannelSendResult


class ChannelRegistry:
    def __init__(self):
        self.channels: dict[str, Channel] = {}

    def register(self, channel: Channel) -> None:
        self.channels[channel.channel_id] = channel

    def get(self, channel_id: str) -> Channel | None:
        return self.channels.get(channel_id)

    def send(self, response: AgentResponse) -> ChannelSendResult:
        channel = self.get(response.channel_id)
        if channel is None:
            return ChannelSendResult(False, f"Channel not found: {response.channel_id}")
        return channel.send(response)

    def list_channels(self) -> list[dict]:
        result = []
        for channel in self.channels.values():
            result.append(
                {
                    "channel_id": channel.channel_id,
                    "kind": channel.kind.value,
                    "trust_level": channel.trust_level.value,
                }
            )
        return result
