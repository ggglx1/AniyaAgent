from .base import AgentResponse, ChannelMessage
from .registry import ChannelRegistry


class ChannelRuntime:
    def __init__(self, agent_runtime, registry: ChannelRegistry):
        self.agent_runtime = agent_runtime
        self.registry = registry

    def handle_message(self, message: ChannelMessage, deliver: bool = True, event_callback=None) -> AgentResponse:
        run_result = self.agent_runtime.handle_message(message, event_callback=event_callback)
        response = AgentResponse(
            channel_id=message.channel_id,
            conversation_id=message.conversation_id,
            run_id=run_result.run_id,
            status=run_result.status,
            text=run_result.output,
            error=run_result.error,
            metadata={
                "session_id": run_result.session_id,
                "user_id": message.user_id,
                "kind": message.kind.value,
                "trust_level": message.trust_level.value,
            },
        )
        if deliver:
            self.registry.send(response)
        return response
