from __future__ import annotations

from datetime import datetime, timezone

from main.runtime.models import UnifiedRunResult


class ProactiveExecutor:
    """Renders structured scheduler events into durable notification outbox entries."""

    supported = {"reminder.due", "routine.due", "daily.review_due", "open_loop.followup_due", "maintenance.notification"}

    def __init__(self, application): self.app = application

    def execute(self, request, context, decision):
        event = dict(request.metadata.get("proactive_event") or {})
        event_type = str(event.get("type") or "")
        if event_type not in self.supported:
            return UnifiedRunResult(request.run_id, "failed", error=f"Unsupported proactive event: {event_type}", error_code="unsupported_proactive_event", metadata={"executor":"proactive"})
        context["token"].check()
        content = str(event.get("content") or event.get("message") or "You have a scheduled AniyaAgent notification.")
        channel = str(event.get("target_channel") or "weixin")
        dispatcher = getattr(self.app.runtime, "reminder_dispatcher", None)
        if dispatcher is None:
            return UnifiedRunResult(request.run_id, "failed", error="Notification dispatcher is unavailable.", error_code="notification_unavailable", metadata={"executor":"proactive"})
        # Durable outbox remains the delivery authority; Web is not treated as a durable target.
        occurrence = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        outbox_id = dispatcher.delivery_outbox.enqueue(
            str(event.get("entity_id") or f"proactive_{request.run_id}"), channel,
            str(event.get("recipient_id") or request.user_id),
            {"text": content, "event_type": event_type, "run_id": request.run_id}, occurrence,
        )
        return UnifiedRunResult(request.run_id, "completed", content, metadata={"executor":"proactive", "outbox_id": outbox_id, "event_type": event_type})
