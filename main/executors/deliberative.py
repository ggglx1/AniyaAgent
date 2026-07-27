from __future__ import annotations

from main.runtime.models import UnifiedRunResult


class DeliberativeExecutor:
    """Compatibility adapter around the existing protected ReAct runtime."""
    def __init__(self, application): self.app = application
    def execute(self, request, context, decision):
        # Channel imports are intentionally delayed to keep runtime composition free of Web cycles.
        from main.channel.base import ChannelMessage
        from main.channel.types import ChannelKind, TrustLevel
        context["token"].check()
        runtime = self.app.web_runtime().agent_runtime
        result = runtime.run_with_context(
            session_id=f"{request.channel_id}:{request.conversation_id}", user_text=request.text,
            channel_context={"channel_id": request.channel_id, "user_id": request.user_id, "conversation_id": request.conversation_id, "kind": "web", "metadata": request.metadata},
            event_callback=context["emit"], external_run_id=request.run_id, cancellation_token=context["token"], acquire_lock=False, archive_facts=False,
        )
        context["token"].check()
        return UnifiedRunResult(request.run_id, result.status, result.output, result.error, metadata={**(result.memory_sources or {}), "executor":"deliberative"})
