from __future__ import annotations

from main.actions import StructuredCommandParser
from .models import RouteDecision, RunRequest


class RunRouter:
    """Ordered routing: explicit mode, pending action, deterministic action, fast path, then open task."""

    def __init__(self, pending_actions=None):
        self.pending_actions = pending_actions
        self.parser = StructuredCommandParser()

    def route(self, request: RunRequest) -> RouteDecision:
        if request.mode == "qa": return RouteDecision("qa", "qa", "knowledge_question", 1.0, "explicit QA mode")
        if request.mode == "coding": return RouteDecision("coding", "coding", "coding_task", 1.0, "explicit Coding mode", required_capabilities=["local_tools"])
        if request.metadata.get("proactive_event"): return RouteDecision("assistant", "proactive", "proactive_event", 1.0, "scheduler event")
        pending = self.pending_actions.get(request.track_id, request.user_id) if self.pending_actions else None
        parsed = self.parser.parse(request.text, request.run_id, pending=pending)
        if pending:
            if self.rejects_pending(request.text):
                self.pending_actions.resolve(request.track_id, owner_id=request.user_id, state="cancelled")
                return RouteDecision("assistant", "direct_conversation", "conversation", 1.0, "user cancelled pending action")
            if parsed:
                request.metadata["structured_command"] = parsed.to_dict(); request.metadata["pending_action_id"] = pending["pending_action_id"]
                confirmed = pending.get("confirmation_state") == "confirmation_required"
                return RouteDecision("assistant", "structured_action", parsed.action, .98, "pending action continuation", requires_confirmation=False if confirmed else False)
            return RouteDecision("assistant", "waiting_input", "pending_action", .9, "pending action needs missing fields", missing_fields=pending["missing_fields"])
        if parsed:
            request.metadata["structured_command"] = parsed.to_dict()
            missing = ["scheduled_at"] if parsed.action == "reminder.create" and not parsed.arguments.get("scheduled_at") else []
            risk = parsed.action.endswith((".delete", ".forget"))
            return RouteDecision("assistant", "structured_action", parsed.action, .96, "structured command", requires_confirmation=risk, missing_fields=missing)
        if self.is_complex(request.text, request.metadata):
            return RouteDecision("assistant", "deliberative_agent", "open_task", .68, "open multi-step task", required_capabilities=["local_tools"])
        return RouteDecision("assistant", "direct_conversation", "conversation", .86, "single-turn fast path")

    def rejects_pending(self, text: str) -> bool:
        return any(value in text.lower() for value in ("取消", "算了", "不用了", "不需要", "cancel"))

    def is_complex(self, text: str, metadata: dict) -> bool:
        if metadata.get("attachment_ids") and len(metadata.get("attachment_ids") or []) > 1: return True
        return any(word in text for word in ("分析", "规划", "比较", "查找", "多个文件", "帮我完成", "一步一步", "多步", "重构", "实现"))
