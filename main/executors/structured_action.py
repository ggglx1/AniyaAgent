from __future__ import annotations

from datetime import datetime

from main.runtime.models import UnifiedRunResult


class StructuredActionExecutor:
    """Domain-service writes for deterministic actions; no ReAct/database tool ownership."""
    def __init__(self, application): self.app = application
    def execute(self, request, context, decision):
        state = self.app.runtime.personal_state; text = request.text.strip()
        if decision.missing_fields:
            return UnifiedRunResult(request.run_id, "pending_confirmation", "请补充提醒的具体日期和时间后，我再为你创建提醒。", metadata={"missing_fields":decision.missing_fields, "executor":"structured_action"})
        if decision.intent == "task":
            if "完成" in text:
                return UnifiedRunResult(request.run_id, "pending_confirmation", "请告诉我要完成的任务 ID，或明确说明任务名称。", metadata={"executor":"structured_action"})
            task = state.create_task(self.clean(text, ("创建任务", "添加任务", "待办")) or text, source_conversation="")
            return UnifiedRunResult(request.run_id, "completed", f"已创建任务：{task.title}", metadata={"executor":"structured_action", "actions":[{"action":"task.create","id":task.id}]})
        if decision.intent == "reminder":
            parsed = self.parse_time(text)
            if not parsed: return UnifiedRunResult(request.run_id, "pending_confirmation", "请提供 ISO 时间或明确的日期与时刻，例如 2026-07-24T15:00:00+08:00。", metadata={"executor":"structured_action"})
            reminder = state.create_reminder(self.clean(text, ("提醒我", "创建提醒")) or text, parsed)
            return UnifiedRunResult(request.run_id, "completed", f"已创建提醒：{reminder.content}", metadata={"executor":"structured_action", "actions":[{"action":"reminder.create","id":reminder.id}]})
        if decision.intent == "memory":
            return UnifiedRunResult(request.run_id, "pending_confirmation", "请通过记忆管理操作确认、纠正或归档具体记忆。", metadata={"executor":"structured_action"})
        return UnifiedRunResult(request.run_id, "pending_confirmation", "这个操作需要更多明确参数。", metadata={"executor":"structured_action"})
    def parse_time(self, text: str) -> str:
        import re
        match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})", text)
        return match.group(0) if match else ""
    def clean(self, text: str, markers: tuple[str, ...]) -> str:
        for marker in markers: text = text.replace(marker, "")
        return text.strip(" ：:，,。")
