from __future__ import annotations

from main.actions import StructuredCommand, StructuredCommandParser
from main.personal.models import PersonalTaskStatus, ReminderStatus
from main.runtime.models import UnifiedRunResult


class StructuredActionExecutor:
    """Executes validated domain commands. It never delegates deterministic writes to ReAct."""

    def __init__(self, application):
        self.app = application
        self.parser = StructuredCommandParser()

    def execute(self, request, context, decision):
        context["token"].check()
        raw = request.metadata.get("structured_command")
        command = StructuredCommand(**raw) if raw else self.parser.parse(request.text, request.run_id)
        if command is None:
            return UnifiedRunResult(request.run_id, "waiting_input", "请说明要执行的操作以及必要参数。", metadata={"executor": "structured_action"})
        store = context["pending_actions"]
        cached = store.command_result(command.idempotency_key)
        if cached:
            return UnifiedRunResult(request.run_id, "completed", cached["output"], metadata={**cached.get("metadata", {}), "executor": "structured_action", "idempotent_replay": True})
        missing = self.missing(command)
        if missing:
            pending = store.create(request.track_id, command.to_dict(), missing, owner_id=request.user_id)
            return UnifiedRunResult(request.run_id, "waiting_input", self.prompt_for(missing), metadata={"executor": "structured_action", "pending_action": pending, "missing_fields": missing})
        if decision.requires_confirmation:
            pending = store.create(request.track_id, command.to_dict(), [], owner_id=request.user_id, confirmation_state="confirmation_required")
            return UnifiedRunResult(request.run_id, "waiting_confirmation", "该操作不可逆。请明确回复“确认”后继续。", metadata={"executor": "structured_action", "pending_action": pending})
        output, metadata = self.dispatch(command)
        store.save_command_result(command.idempotency_key, {"output": output, "metadata": metadata})
        if request.metadata.get("pending_action_id"):
            store.resolve(request.track_id, owner_id=request.user_id)
        return UnifiedRunResult(request.run_id, "completed", output, metadata={"executor": "structured_action", "command": command.to_dict(), **metadata})

    def missing(self, command: StructuredCommand) -> list[str]:
        if command.action == "task.create" and not str(command.arguments.get("title") or "").strip(): return ["title"]
        if command.action == "reminder.create":
            return [item for item in ("content", "scheduled_at") if not str(command.arguments.get(item) or "").strip()]
        if command.action.endswith((".complete", ".cancel", ".delete", ".update", ".snooze", ".pause", ".resume", ".confirm", ".correct", ".archive", ".forget")) and not command.arguments.get("id"):
            return ["id"]
        return []

    def prompt_for(self, fields: list[str]) -> str:
        labels = {"scheduled_at": "日期和时间", "content": "提醒内容", "title": "任务名称", "id": "目标 ID"}
        return "请补充" + "、".join(labels.get(field, field) for field in fields) + "后继续。"

    def dispatch(self, command: StructuredCommand) -> tuple[str, dict]:
        state = self.app.runtime.personal_state
        action, args = command.action, command.arguments
        if action == "task.create":
            item = state.create_task(args["title"], source_conversation=command.source_message_id)
            return f"已创建任务：{item.title}", {"executed_actions": [{"action": action, "id": item.id}]}
        if action == "task.query":
            tasks = state.list_tasks(limit=20)
            return self.list_output("任务", tasks, "title"), {"executed_actions": [{"action": action}]}
        if action == "task.complete":
            item = state.complete_task(args["id"])
            return f"已完成任务：{item.title}", {"executed_actions": [{"action": action, "id": item.id}]}
        if action in {"task.cancel", "task.delete"}:
            item = state.update_task(args["id"], {"status": PersonalTaskStatus.CANCELLED.value})
            return f"已取消任务：{item.title}", {"executed_actions": [{"action": action, "id": item.id}]}
        if action == "reminder.create":
            item = state.create_reminder(args["content"], args["scheduled_at"], timezone_name=args.get("timezone", "Asia/Shanghai"), target_channel=args.get("target_channel", "weixin"))
            return f"已创建提醒：{item.content}（{item.scheduled_at}，将通过 {item.target_channel} 通知）", {"executed_actions": [{"action": action, "id": item.id}]}
        if action == "reminder.query":
            return self.list_output("提醒", state.list_reminders(limit=20), "content"), {"executed_actions": [{"action": action}]}
        if action in {"reminder.cancel", "reminder.delete"}:
            item = state.update_reminder(args["id"], {"status": ReminderStatus.CANCELLED.value})
            return f"已取消提醒：{item.content}", {"executed_actions": [{"action": action, "id": item.id}]}
        if action == "reminder.snooze":
            item = state.snooze_reminder(args["id"], args["scheduled_at"])
            return f"已延后提醒：{item.content}", {"executed_actions": [{"action": action, "id": item.id}]}
        if action.startswith("memory."):
            manager = self.app.runtime.personal_memory_manager
            operation = action.split(".", 1)[1]
            item = {"confirm": manager.confirm, "archive": manager.archive, "forget": manager.forget}.get(operation)
            if item is None: raise ValueError("Memory correction requires replacement text through the memory API.")
            record = item(args["id"])
            return f"已{operation}记忆：{record.id}", {"executed_actions": [{"action": action, "id": record.id}]}
        if action.startswith("routine."):
            routines = self.app.runtime.routine_manager
            if action == "routine.query": return self.list_output("例行", routines.list(limit=20), "name"), {"executed_actions": [{"action": action}]}
            item = routines.require(args["id"])
            enabled = action == "routine.resume"
            updated = routines.update(item.id, {"enabled": enabled})
            return f"已{'恢复' if enabled else '暂停'}例行：{updated.name}", {"executed_actions": [{"action": action, "id": updated.id}]}
        raise ValueError(f"Unsupported structured action: {action}")

    def list_output(self, label: str, items: list, field: str) -> str:
        if not items: return f"当前没有{label}。"
        return "\n".join([f"{label}："] + [f"- [{item.id}] {getattr(item, field)}" for item in items])
