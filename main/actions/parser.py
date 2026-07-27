from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import StructuredCommand


class StructuredCommandParser:
    """Conservative parser: it extracts explicit commands and asks when an entity is ambiguous."""

    ACTIONS = {
        "task": {"create": ("创建任务", "添加任务", "新建任务", "待办"), "query": ("查看任务", "查询任务", "任务列表"), "complete": ("完成任务",), "cancel": ("取消任务",), "delete": ("删除任务",), "update": ("修改任务", "更新任务")},
        "reminder": {"create": ("提醒我", "创建提醒", "添加提醒"), "query": ("查看提醒", "查询提醒", "提醒列表"), "cancel": ("取消提醒",), "delete": ("删除提醒",), "snooze": ("稍后提醒", "延后提醒"), "update": ("修改提醒", "更新提醒")},
        "routine": {"create": ("创建例行", "添加例行"), "query": ("查看例行", "例行列表"), "pause": ("暂停例行",), "resume": ("恢复例行",), "delete": ("删除例行",), "update": ("修改例行",)},
        "memory": {"confirm": ("确认记忆",), "correct": ("纠正记忆",), "archive": ("归档记忆",), "forget": ("忘记记忆", "删除记忆")},
    }
    NEGATED = ("假如", "如果", "怎么做", "会怎样", "示例", "测试说明", "不要创建", "不用创建")

    def parse(self, text: str, run_id: str, *, timezone_name: str = "Asia/Shanghai", pending: dict | None = None) -> StructuredCommand | None:
        text = text.strip()
        if pending is not None:
            previous = dict(pending["command"])
            arguments = dict(previous.get("arguments") or {})
            if pending.get("confirmation_state") == "confirmation_required" and text.lower() in {"确认", "确认执行", "confirm", "yes", "是"}:
                return self.command(run_id, previous["action"], arguments, text)
            if "scheduled_at" in pending.get("missing_fields", []):
                parsed = self.parse_time(text, timezone_name)
                if parsed:
                    arguments["scheduled_at"] = parsed
                    return self.command(run_id, previous["action"], arguments, text)
            return None
        if any(marker in text for marker in self.NEGATED) and "不要忘了提醒我" not in text:
            return None
        for domain, operations in self.ACTIONS.items():
            for operation, markers in operations.items():
                if any(marker in text for marker in markers):
                    action = f"{domain}.{operation}"
                    args = self.arguments(action, text, timezone_name)
                    return self.command(run_id, action, args, text)
        return None

    def command(self, run_id: str, action: str, arguments: dict, text: str) -> StructuredCommand:
        fingerprint = hashlib.sha256(f"{run_id}:{action}:{arguments}".encode()).hexdigest()
        return StructuredCommand(f"cmd_{uuid.uuid4().hex[:16]}", run_id, action=action, arguments=arguments, idempotency_key=fingerprint)

    def arguments(self, action: str, text: str, timezone_name: str) -> dict:
        entity_id = self.entity_id(text)
        if action == "reminder.create":
            content = self.strip_markers(text, self.ACTIONS["reminder"]["create"])
            return {"content": content, "scheduled_at": self.parse_time(text, timezone_name), "timezone": timezone_name, "target_channel": "weixin"}
        if action == "task.create":
            return {"title": self.strip_markers(text, self.ACTIONS["task"]["create"])}
        if action.endswith((".complete", ".cancel", ".delete", ".update", ".snooze", ".pause", ".resume", ".confirm", ".correct", ".archive", ".forget")):
            return {"id": entity_id, "text": text, "scheduled_at": self.parse_time(text, timezone_name)}
        return {}

    def parse_time(self, text: str, timezone_name: str) -> str:
        zone = ZoneInfo(timezone_name); now = datetime.now(zone)
        iso = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})", text)
        if iso:
            return iso.group(0)
        day = now.date()
        if "后天" in text: day += timedelta(days=2)
        elif "明天" in text: day += timedelta(days=1)
        elif "今天" not in text and not re.search(r"(?:上午|下午|晚上|中午|\d{1,2}点|\d{1,2}:\d{2})", text): return ""
        normalized = self.normalize_chinese_clock(text)
        match = re.search(r"(\d{1,2})(?:[:：](\d{2}))?\s*(?:点|时)?", normalized)
        if not match: return ""
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if ("下午" in text or "晚上" in text) and hour < 12: hour += 12
        if "中午" in text and hour < 11: hour += 12
        try: return datetime.combine(day, datetime.min.time(), tzinfo=zone).replace(hour=hour, minute=minute).isoformat()
        except ValueError: return ""

    def normalize_chinese_clock(self, text: str) -> str:
        numbers = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        match = re.search(r"([零一二两三四五六七八九十]+)(?:点|时)", text)
        if not match:
            return text
        value = match.group(1)
        if value == "十": hour = 10
        elif value.startswith("十"): hour = 10 + numbers.get(value[1:], 0)
        elif value.endswith("十"): hour = numbers.get(value[0], 0) * 10
        elif "十" in value: hour = numbers.get(value[0], 0) * 10 + numbers.get(value[-1], 0)
        else: hour = numbers.get(value, -1)
        return text.replace(match.group(0), f"{hour}点") if hour >= 0 else text

    def entity_id(self, text: str) -> str:
        match = re.search(r"\b(?:ptask|rem|routine|mem)_[a-f0-9]{6,}\b", text)
        return match.group(0) if match else ""

    def strip_markers(self, text: str, markers: tuple[str, ...]) -> str:
        value = text
        for marker in markers: value = value.replace(marker, "")
        return re.sub(r"\s+", " ", value).strip(" ：:，,。")
