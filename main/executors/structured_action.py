from __future__ import annotations

from main.actions import ActionCommandService, ActionRegistry, StructuredCommand
from main.runtime.models import UnifiedRunResult


class StructuredActionExecutor:
    """Executes only registered deterministic actions; free-form ReAct is never a fallback."""

    def __init__(self, application):
        self.app = application
        self.commands = ActionCommandService(application)

    def execute(self, request, context, decision):
        context["token"].check()
        raw = request.metadata.get("structured_command")
        command = StructuredCommand(**raw) if raw else None
        if command is None or not ActionRegistry.supports(command.action):
            return UnifiedRunResult(request.run_id, "waiting_input", "Please provide a supported action and its required fields.", metadata={"executor": "structured_action"})
        store = context["pending_actions"]
        cached = store.command_result(command.idempotency_key)
        if cached:
            return UnifiedRunResult(request.run_id, "completed", cached["output"], metadata={**cached.get("metadata", {}), "executor": "structured_action", "idempotent_replay": True})
        resolution = dict(request.metadata.get("intent_resolution") or {})
        pending_id = str(request.metadata.get("pending_action_id") or "")
        if str(resolution.get("status") or "") == "ambiguous":
            missing = list(resolution.get("missing_fields") or ["id"])
            candidates = list(resolution.get("ambiguous_entities") or [])
            if candidates:
                command.result = {**command.result, "allowed_entity_ids": candidates}
            pending = store.update(pending_id, command.to_dict(), missing) if pending_id else store.create(request.track_id, command.to_dict(), missing, owner_id=request.user_id, source_run_id=request.run_id, source_message_id=command.source_message_id, risk_level=command.risk_level)
            prompt = "Please choose the exact target ID" + (": " + ", ".join(candidates) if candidates else ".")
            receipt = self.commands.record(command, "waiting_input", prompt, source="agent", channel_id=request.channel_id, details={"missing_fields": missing, "entity_candidates": candidates})["receipt"]
            return UnifiedRunResult(request.run_id, "waiting_input", prompt, metadata={"executor": "structured_action", "pending_action": pending, "missing_fields": missing, "entity_candidates": candidates, "intent_resolution": resolution, "action_receipt": receipt})
        missing = ActionRegistry.missing(command.action, command.arguments)
        if missing:
            if pending_id:
                pending = store.update(pending_id, command.to_dict(), missing)
            else:
                pending = store.create(request.track_id, command.to_dict(), missing, owner_id=request.user_id, source_run_id=request.run_id, source_message_id=command.source_message_id, risk_level=command.risk_level)
            output = self.prompt_for(missing)
            receipt = self.commands.record(command, "waiting_input", output, source="agent", channel_id=request.channel_id, details={"missing_fields": missing})["receipt"]
            return UnifiedRunResult(request.run_id, "waiting_input", output, metadata={"executor": "structured_action", "pending_action": pending, "missing_fields": missing, "action_receipt": receipt})
        spec = ActionRegistry.get(command.action)
        preview_status = str(resolution.get("status") or "")
        pending_state = str(request.metadata.get("pending_confirmation_state") or "")
        if spec and spec.preview_required and preview_status in {"preview_required", "confirmation_required"} and (not pending_id or pending_state == "missing_input"):
            state = "confirmation_required" if spec.requires_confirmation else "preview_required"
            pending = store.update(pending_id, command.to_dict(), [], confirmation_state=state) if pending_id else store.create(request.track_id, command.to_dict(), [], owner_id=request.user_id, confirmation_state=state, source_run_id=request.run_id, source_message_id=command.source_message_id, risk_level=spec.risk_level)
            output = self.preview(command, spec.execution_policy)
            receipt = self.commands.record(command, state, output, source="agent", channel_id=request.channel_id, details={"pending_action_id": pending["pending_action_id"], "track_id": request.track_id, "owner_id": request.user_id})["receipt"]
            return UnifiedRunResult(request.run_id, "waiting_confirmation", output, metadata={"executor": "structured_action", "pending_action": pending, "action_preview": True, "intent_resolution": resolution, "action_receipt": receipt})
        result = self.commands.execute(command, source="agent", channel_id=request.channel_id, confirmed=True)
        output = result["output"]
        metadata = {"executed_actions": result.get("executed_actions", []), "action_receipt": result["receipt"]}
        store.save_command_result(command.idempotency_key, {"output": output, "metadata": metadata})
        if pending_id:
            store.resolve(request.track_id, owner_id=request.user_id)
        return UnifiedRunResult(request.run_id, "completed", output, metadata={"executor": "structured_action", "command": command.to_dict(), "intent_resolution": resolution, **metadata})

    def prompt_for(self, fields: list[str]) -> str:
        labels = {"scheduled_at": "time", "content": "reminder content", "title": "task title", "id": "target ID", "replacement_text": "replacement text", "name": "routine name", "routine_type": "routine type", "cron": "cron schedule"}
        return "Please provide " + ", ".join(labels.get(field, field) for field in fields) + "."

    def preview(self, command: StructuredCommand, policy: str) -> str:
        details = ", ".join(f"{key}={value}" for key, value in command.arguments.items() if value not in (None, ""))
        return f"Action preview: {command.action} ({details}). Risk: {policy}. Reply 'confirm' to execute, or cancel/modify it."
