from __future__ import annotations

from main.actions.intent import IntentResolutionService
from .models import RouteDecision, RunRequest


class RunRouter:
    """Ordered routing: explicit mode, pending action, deterministic action, fast path, then open task."""

    def __init__(self, application, pending_actions=None):
        self.pending_actions = pending_actions
        self.intent = IntentResolutionService(application)

    def route(self, request: RunRequest, cancellation_token=None) -> RouteDecision:
        if request.mode == "qa": return RouteDecision("qa", "qa", "knowledge_question", 1.0, "explicit QA mode")
        if request.mode == "coding": return RouteDecision("coding", "coding", "coding_task", 1.0, "explicit Coding mode", required_capabilities=["local_tools"])
        if request.metadata.get("proactive_event"): return RouteDecision("assistant", "proactive", "proactive_event", 1.0, "scheduler event")
        pending = self.pending_actions.get(request.track_id, request.user_id) if self.pending_actions else None
        if pending:
            if self.rejects_pending(request.text):
                self.pending_actions.resolve(request.track_id, owner_id=request.user_id, state="cancelled")
                request.metadata["cancel_waiting_run_id"] = str(pending.get("source_run_id") or "")
                return RouteDecision("assistant", "direct_conversation", "conversation", 1.0, "user cancelled pending action")
        resolution = self.intent.resolve(request, pending, cancellation_token=cancellation_token)
        request.metadata["intent_resolution"] = resolution.to_dict()
        if pending:
            if resolution.status in {"ready", "preview_required", "confirmation_required", "ambiguous"} and resolution.validated_action:
                source_run_id = str(pending.get("source_run_id") or "")
                if source_run_id:
                    request.metadata["supersedes_waiting_run_id"] = source_run_id
                request.metadata["structured_command"] = resolution.validated_action.to_dict(); request.metadata["pending_action_id"] = pending["pending_action_id"]; request.metadata["pending_confirmation_state"] = pending.get("confirmation_state", "")
                return RouteDecision("assistant", "structured_action", resolution.validated_action.action, .98, "pending action continuation")
            return RouteDecision("assistant", "waiting_input", "pending_action", .9, "pending action needs missing fields", missing_fields=pending["missing_fields"])
        if resolution.status in {"ready", "missing_fields", "preview_required", "confirmation_required"} and resolution.validated_action:
            request.metadata["structured_command"] = resolution.validated_action.to_dict()
            return RouteDecision("assistant", "structured_action", resolution.validated_action.action, .96, f"intent resolution: {resolution.status}", missing_fields=resolution.missing_fields)
        if resolution.status == "ambiguous" and resolution.validated_action and resolution.missing_fields:
            request.metadata["structured_command"] = resolution.validated_action.to_dict()
            return RouteDecision("assistant", "structured_action", resolution.validated_action.action, .95, resolution.policy_reason, missing_fields=resolution.missing_fields)
        if resolution.status == "ambiguous":
            return RouteDecision("assistant", "waiting_input", "ambiguous_action", .95, resolution.policy_reason, missing_fields=resolution.missing_fields)
        if request.metadata.get("force_deliberative"):
            return RouteDecision("assistant", "deliberative_agent", "benchmark_open_task", 1.0, "benchmark forced deliberative route", required_capabilities=["filesystem_read", "filesystem_write", "shell_readonly"])
        if self.is_complex(request.text, request.metadata):
            # Keep both read and potential action categories discoverable to the
            # controlled two-phase executor without sending action schemas first.
            required = ["filesystem_read", "filesystem_write", "shell_readonly"]
            if request.metadata.get("allow_mcp"):
                required.append("mcp")
            return RouteDecision("assistant", "deliberative_agent", "open_task", .68, "open multi-step task", required_capabilities=required)
        return RouteDecision("assistant", "direct_conversation", "conversation", .86, "single-turn fast path")

    def rejects_pending(self, text: str) -> bool:
        return any(value in text.lower() for value in ("取消", "算了", "不用了", "不需要", "cancel"))

    def is_complex(self, text: str, metadata: dict) -> bool:
        if metadata.get("attachment_ids") and len(metadata.get("attachment_ids") or []) > 1: return True
        return any(word in text for word in ("分析", "规划", "比较", "查找", "多个文件", "帮我完成", "一步一步", "多步", "重构", "实现"))
