from __future__ import annotations

from main.actions import ActionRegistry, StructuredCommand
from main.personal.models import PersonalTaskStatus, ReminderStatus
from main.runtime.models import UnifiedRunResult


class StructuredActionExecutor:
    """Executes only registered deterministic actions; free-form ReAct is never a fallback."""

    def __init__(self, application):
        self.app = application

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
            return UnifiedRunResult(request.run_id, "waiting_input", prompt, metadata={"executor": "structured_action", "pending_action": pending, "missing_fields": missing, "entity_candidates": candidates, "intent_resolution": resolution})
        missing = ActionRegistry.missing(command.action, command.arguments)
        if missing:
            if pending_id:
                pending = store.update(pending_id, command.to_dict(), missing)
            else:
                pending = store.create(request.track_id, command.to_dict(), missing, owner_id=request.user_id, source_run_id=request.run_id, source_message_id=command.source_message_id, risk_level=command.risk_level)
            return UnifiedRunResult(request.run_id, "waiting_input", self.prompt_for(missing), metadata={"executor": "structured_action", "pending_action": pending, "missing_fields": missing})
        spec = ActionRegistry.get(command.action)
        preview_status = str(resolution.get("status") or "")
        pending_state = str(request.metadata.get("pending_confirmation_state") or "")
        if spec and spec.preview_required and preview_status in {"preview_required", "confirmation_required"} and (not pending_id or pending_state == "missing_input"):
            state = "confirmation_required" if spec.requires_confirmation else "preview_required"
            pending = store.update(pending_id, command.to_dict(), [], confirmation_state=state) if pending_id else store.create(request.track_id, command.to_dict(), [], owner_id=request.user_id, confirmation_state=state, source_run_id=request.run_id, source_message_id=command.source_message_id, risk_level=spec.risk_level)
            return UnifiedRunResult(request.run_id, "waiting_confirmation", self.preview(command, spec.execution_policy), metadata={"executor": "structured_action", "pending_action": pending, "action_preview": True, "intent_resolution": resolution})
        output, metadata = self.dispatch(command)
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

    def dispatch(self, command: StructuredCommand) -> tuple[str, dict]:
        state = self.app.runtime.personal_state
        action, args = command.action, dict(command.arguments)
        if action == "task.create":
            item = state.create_task(args["title"], **self.only(args, {"description", "priority", "due_at", "next_action"}), source_conversation=command.source_message_id)
            return self.done(action, item, f"Created task: {item.title}")
        if action == "task.query": return self.list_output("Tasks", state.list_tasks(limit=20), "title"), {"executed_actions": [{"action": action}]}
        if action == "task.update":
            item = state.update_task(args["id"], self.only(args, {"title", "description", "priority", "due_at", "next_action", "status"}))
            return self.done(action, item, f"Updated task: {item.title}")
        if action == "task.complete": return self.done(action, state.complete_task(args["id"]), "Completed task")
        if action == "task.cancel":
            item = state.update_task(args["id"], {"status": PersonalTaskStatus.CANCELLED.value})
            return self.done(action, item, "Cancelled task")
        if action == "task.delete": return self.done(action, state.delete_task(args["id"]), "Deleted task")
        if action == "reminder.create":
            item = state.create_reminder(args["content"], args["scheduled_at"], timezone_name=args.get("timezone", "Asia/Shanghai"), target_channel=args.get("target_channel", "web"), recurrence=args.get("recurrence", ""))
            return self.done(action, item, f"Created reminder: {item.content}")
        if action == "reminder.query": return self.list_output("Reminders", state.list_reminders(limit=20), "content"), {"executed_actions": [{"action": action}]}
        if action == "reminder.update":
            item = state.update_reminder(args["id"], self.only(args, {"content", "scheduled_at", "timezone", "target_channel", "recurrence", "status"}))
            return self.done(action, item, f"Updated reminder: {item.content}")
        if action == "reminder.snooze": return self.done(action, state.snooze_reminder(args["id"], args["scheduled_at"]), "Snoozed reminder")
        if action == "reminder.cancel":
            item = state.update_reminder(args["id"], {"status": ReminderStatus.CANCELLED.value})
            return self.done(action, item, "Cancelled reminder")
        if action == "reminder.delete": return self.done(action, state.delete_reminder(args["id"]), "Deleted reminder")
        if action.startswith("routine."):
            routines = self.app.runtime.routine_manager
            if action == "routine.query": return self.list_output("Routines", routines.list(limit=20), "name"), {"executed_actions": [{"action": action}]}
            if action == "routine.create":
                item = routines.create(args["name"], args["routine_type"], args["cron"], timezone_name=args.get("timezone", "Asia/Shanghai"), target_channel=args.get("target_channel", "web"))
                return self.done(action, item, f"Created routine: {item.name}")
            if action == "routine.update": return self.done(action, routines.update(args["id"], self.only(args, {"name", "routine_type", "cron", "timezone", "target_channel", "enabled"})), "Updated routine")
            if action == "routine.delete": return self.done(action, routines.delete(args["id"]), "Deleted routine")
            item = routines.update(args["id"], {"enabled": action == "routine.resume"})
            return self.done(action, item, "Resumed routine" if action == "routine.resume" else "Paused routine")
        if action.startswith("memory."):
            manager = self.app.runtime.personal_memory_manager
            operation = action.split(".", 1)[1]
            if operation == "correct": record = manager.supersede(args["id"], args["replacement_text"])
            else: record = {"confirm": manager.confirm, "archive": manager.archive, "forget": manager.forget}[operation](args["id"])
            return self.done(action, record, f"{operation.title()} memory: {record.id}")
        raise ValueError(f"Unsupported structured action: {action}")

    def only(self, values: dict, allowed: set[str]) -> dict:
        return {key: value for key, value in values.items() if key in allowed and value not in (None, "")}

    def done(self, action, item, output: str) -> tuple[str, dict]:
        return output, {"executed_actions": [{"action": action, "id": item.id}]}

    def list_output(self, label: str, items: list, field: str) -> str:
        if not items: return f"No {label.lower()}."
        return "\n".join([f"{label}:"] + [f"- [{item.id}] {getattr(item, field)}" for item in items])
