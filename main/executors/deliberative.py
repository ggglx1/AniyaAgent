from __future__ import annotations

from main.runtime.models import UnifiedRunResult


class DeliberativeExecutor:
    """Compatibility adapter around the existing protected ReAct runtime."""
    def __init__(self, application): self.app = application
    def execute(self, request, context, decision):
        # Channel imports are intentionally delayed to keep runtime composition free of Web cycles.
        from main.channel.base import ChannelMessage
        from main.channel.types import ChannelKind, TrustLevel
        message = ChannelMessage("web", request.user_id, request.conversation_id, request.text, ChannelKind.WEB, TrustLevel.HIGH, metadata={**request.metadata, "executor":"deliberative"})
        result = self.app.web_runtime().handle_message(message, deliver=False, event_callback=context["emit"])
        return UnifiedRunResult(request.run_id, result.status, result.text, result.error, {**result.metadata, "executor":"deliberative"})
