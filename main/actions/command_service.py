from __future__ import annotations

import uuid
from datetime import datetime, timezone

from main.actions.models import StructuredCommand
from main.actions.registry import ActionRegistry
from main.personal.models import PersonalTaskStatus, ReminderStatus


class ActionCommandService:
    """The single deterministic write path for chat and agenda actions."""

    TERMINAL = {"succeeded", "failed", "cancelled", "undone"}

    def __init__(self, application):
        self.app = application

    def execute(self, command: StructuredCommand, *, source: str, channel_id: str = "", confirmed: bool = False, receipt_id: str = "") -> dict:
        error = ActionRegistry.validate_arguments(command.action, command.arguments)
        missing = ActionRegistry.missing(command.action, command.arguments)
        if error or missing:
            return self.record(command, "waiting_input", "Please provide the required action fields.", source=source, channel_id=channel_id, receipt_id=receipt_id, details={"error": error, "missing_fields": missing})
        if not receipt_id:
            previous = self.app.runtime.personal_state.repository.find_receipt_by_key(self.app.runtime.personal_state.user_id, command.idempotency_key)
            if previous:
                return {"receipt": previous, "output": previous.get("summary", ""), "idempotent_replay": True}
        spec = ActionRegistry.get(command.action)
        if spec is None:
            return self.record(command, "failed", "Unsupported action.", source=source, channel_id=channel_id, receipt_id=receipt_id)
        if spec.preview_required and not confirmed:
            status = "confirmation_required" if spec.requires_confirmation else "preview_required"
            return self.record(command, status, self.preview(command, spec.execution_policy), source=source, channel_id=channel_id, receipt_id=receipt_id)
        receipt = self.record(command, "accepted", "Action accepted.", source=source, channel_id=channel_id, receipt_id=receipt_id)
        try:
            output, entity_type, entity_id = self.dispatch(command)
        except Exception as exc:
            failed = self.update(receipt["receipt"], "failed", f"Action failed: {exc}", details={"error": str(exc)})
            return {"receipt": failed, "output": failed["summary"], "error": str(exc)}
        succeeded = self.update(receipt["receipt"], "succeeded", output, entity_type=entity_type, entity_id=entity_id, completed=True)
        return {"receipt": succeeded, "output": output, "executed_actions": [{"action": command.action, "id": entity_id}] if entity_id else []}

    def confirm(self, receipt_id: str, *, source: str, channel_id: str = "") -> dict:
        repository = self.app.runtime.personal_state.repository
        receipt = repository.get_receipt(receipt_id, self.app.runtime.personal_state.user_id)
        if receipt is None:
            raise FileNotFoundError("Action receipt not found")
        if receipt["status"] == "succeeded":
            return {"receipt": receipt, "output": receipt["summary"], "idempotent_replay": True}
        if receipt["status"] not in {"preview_required", "confirmation_required"}:
            raise ValueError("This action receipt cannot be confirmed")
        raw = dict(receipt.get("details") or {}).get("command")
        if not isinstance(raw, dict):
            raise ValueError("Action receipt has no executable command")
        result = self.execute(StructuredCommand(**raw), source=source, channel_id=channel_id, confirmed=True, receipt_id=receipt_id)
        if result["receipt"].get("status") == "succeeded":
            self.resolve_pending(result["receipt"])
        return result

    def cancel(self, receipt_id: str) -> dict:
        repository = self.app.runtime.personal_state.repository
        receipt = repository.get_receipt(receipt_id, self.app.runtime.personal_state.user_id)
        if receipt is None:
            raise FileNotFoundError("Action receipt not found")
        if receipt["status"] == "cancelled":
            return {"receipt": receipt, "output": receipt.get("summary", ""), "idempotent_replay": True}
        if receipt["status"] not in {"waiting_input", "preview_required", "confirmation_required"}:
            raise ValueError("This action receipt cannot be cancelled")
        cancelled = self.update(receipt, "cancelled", "Action cancelled.", completed=True)
        self.resolve_pending(cancelled, state="cancelled")
        return {"receipt": cancelled, "output": cancelled["summary"]}

    def record(self, command: StructuredCommand, status: str, summary: str, *, source: str, channel_id: str = "", receipt_id: str = "", details: dict | None = None) -> dict:
        repository = self.app.runtime.personal_state.repository
        current = repository.get_receipt(receipt_id, self.app.runtime.personal_state.user_id) if receipt_id else None
        payload = {"command": command.to_dict(), "source": source, **(details or {})}
        if current:
            return {"receipt": self.update(current, status, summary, details=payload)}
        receipt = {
            "receipt_id": f"receipt_{uuid.uuid4().hex[:16]}", "user_id": self.app.runtime.personal_state.user_id,
            "run_id": command.run_id, "command_id": command.command_id, "idempotency_key": command.idempotency_key,
            "source_message_id": command.source_message_id, "channel_id": channel_id, "action": command.action,
            "entity_type": self.entity_type(command.action), "entity_id": str(command.arguments.get("id") or ""),
            "status": status, "summary": summary, "details": payload, "created_at": self.now(),
            "completed_at": "", "undo_until": "", "parent_receipt_id": "",
        }
        return {"receipt": repository.save_receipt(receipt)}

    def update(self, receipt: dict, status: str, summary: str, *, entity_type: str = "", entity_id: str = "", completed: bool = False, details: dict | None = None) -> dict:
        changes = {"status": status, "summary": summary}
        if entity_type: changes["entity_type"] = entity_type
        if entity_id: changes["entity_id"] = entity_id
        if details is not None: changes["details"] = {**dict(receipt.get("details") or {}), **details}
        if completed: changes["completed_at"] = self.now()
        return self.app.runtime.personal_state.repository.update_receipt(receipt["receipt_id"], receipt["user_id"], **changes)

    def dispatch(self, command: StructuredCommand) -> tuple[str, str, str]:
        state, action, args = self.app.runtime.personal_state, command.action, dict(command.arguments)
        if action == "task.create":
            item = state.create_task(args["title"], **self.only(args, {"description", "priority", "due_at", "next_action"}), source_conversation=command.source_message_id)
            return f"Created task: {item.title}", "task", item.id
        if action == "task.query": return self.list_output("Tasks", state.list_tasks(limit=20), "title"), "", ""
        if action == "task.update":
            item = state.update_task(args["id"], self.only(args, {"title", "description", "priority", "due_at", "next_action", "status"}))
            return f"Updated task: {item.title}", "task", item.id
        if action == "task.complete":
            item = state.complete_task(args["id"]); return "Completed task", "task", item.id
        if action == "task.cancel":
            item = state.update_task(args["id"], {"status": PersonalTaskStatus.CANCELLED.value}); return "Cancelled task", "task", item.id
        if action == "task.delete":
            item = state.delete_task(args["id"]); return "Deleted task", "task", item.id
        if action == "reminder.create":
            item = state.create_reminder(args["content"], args["scheduled_at"], timezone_name=args.get("timezone", "Asia/Shanghai"), target_channel=args.get("target_channel", "web"), recurrence=args.get("recurrence", ""))
            return f"Created reminder: {item.content}", "reminder", item.id
        if action == "reminder.query": return self.list_output("Reminders", state.list_reminders(limit=20), "content"), "", ""
        if action == "reminder.update":
            item = state.update_reminder(args["id"], self.only(args, {"content", "scheduled_at", "timezone", "target_channel", "recurrence", "status"}))
            return f"Updated reminder: {item.content}", "reminder", item.id
        if action == "reminder.snooze":
            item = state.snooze_reminder(args["id"], args["scheduled_at"]); return "Snoozed reminder", "reminder", item.id
        if action == "reminder.cancel":
            item = state.update_reminder(args["id"], {"status": ReminderStatus.CANCELLED.value}); return "Cancelled reminder", "reminder", item.id
        if action == "reminder.delete":
            item = state.delete_reminder(args["id"]); return "Deleted reminder", "reminder", item.id
        if action.startswith("routine."):
            routines = self.app.runtime.routine_manager
            if action == "routine.query": return self.list_output("Routines", routines.list(limit=20), "name"), "", ""
            if action == "routine.create": item = routines.create(args["name"], args["routine_type"], args["cron"], timezone_name=args.get("timezone", "Asia/Shanghai"), target_channel=args.get("target_channel", "web"))
            elif action == "routine.update": item = routines.update(args["id"], self.only(args, {"name", "routine_type", "cron", "timezone", "target_channel", "enabled"}))
            elif action == "routine.delete": item = routines.delete(args["id"])
            else: item = routines.update(args["id"], {"enabled": action == "routine.resume"})
            return ("Created routine" if action == "routine.create" else "Updated routine"), "routine", item.id
        if action.startswith("memory."):
            manager, operation = self.app.runtime.personal_memory_manager, action.split(".", 1)[1]
            record = manager.supersede(args["id"], args["replacement_text"]) if operation == "correct" else {"confirm": manager.confirm, "archive": manager.archive, "forget": manager.forget}[operation](args["id"])
            return f"{operation.title()} memory: {record.id}", "memory", record.id
        raise ValueError(f"Unsupported structured action: {action}")

    def preview(self, command: StructuredCommand, policy: str) -> str:
        details = ", ".join(f"{key}={value}" for key, value in command.arguments.items() if value not in (None, ""))
        return f"Action preview: {command.action} ({details}). Risk: {policy}. Confirm to execute."

    def only(self, values: dict, allowed: set[str]) -> dict:
        return {key: value for key, value in values.items() if key in allowed and value not in (None, "")}

    def list_output(self, label: str, items: list, field: str) -> str:
        return f"No {label.lower()}." if not items else "\n".join([f"{label}:"] + [f"- [{item.id}] {getattr(item, field)}" for item in items])

    def entity_type(self, action: str) -> str:
        return action.split(".", 1)[0] if "." in action else ""

    def resolve_pending(self, receipt: dict, state: str = "completed") -> None:
        details = dict(receipt.get("details") or {})
        track_id = str(details.get("track_id") or "")
        if track_id:
            self.app.runtime.pending_actions.resolve(track_id, owner_id=str(details.get("owner_id") or "local"), state=state)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
