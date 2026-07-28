from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import StructuredCommand
from .registry import ActionRegistry


class StructuredCommandParser:
    """Conservative deterministic parser for the ActionRegistry contract."""

    ACTIONS = {
        "task": {
            "create": ("\u521b\u5efa\u4efb\u52a1", "\u6dfb\u52a0\u4efb\u52a1", "\u65b0\u5efa\u4efb\u52a1", "\u5f85\u529e"),
            "query": ("\u67e5\u770b\u4efb\u52a1", "\u67e5\u8be2\u4efb\u52a1", "\u4efb\u52a1\u5217\u8868"),
            "complete": ("\u5b8c\u6210\u4efb\u52a1",), "cancel": ("\u53d6\u6d88\u4efb\u52a1",),
            "delete": ("\u5220\u9664\u4efb\u52a1",), "update": ("\u4fee\u6539\u4efb\u52a1", "\u66f4\u65b0\u4efb\u52a1"),
        },
        "reminder": {
            "create": ("\u63d0\u9192\u6211", "\u521b\u5efa\u63d0\u9192", "\u6dfb\u52a0\u63d0\u9192"),
            "query": ("\u67e5\u770b\u63d0\u9192", "\u67e5\u8be2\u63d0\u9192", "\u63d0\u9192\u5217\u8868"),
            "cancel": ("\u53d6\u6d88\u63d0\u9192",), "delete": ("\u5220\u9664\u63d0\u9192",),
            "snooze": ("\u7a0d\u540e\u63d0\u9192", "\u5ef6\u540e\u63d0\u9192"), "update": ("\u4fee\u6539\u63d0\u9192", "\u66f4\u65b0\u63d0\u9192"),
        },
        "routine": {
            "create": ("\u521b\u5efa\u4f8b\u884c", "\u6dfb\u52a0\u4f8b\u884c"), "query": ("\u67e5\u770b\u4f8b\u884c", "\u4f8b\u884c\u5217\u8868"),
            "pause": ("\u6682\u505c\u4f8b\u884c",), "resume": ("\u6062\u590d\u4f8b\u884c",), "delete": ("\u5220\u9664\u4f8b\u884c",),
            "update": ("\u4fee\u6539\u4f8b\u884c", "\u66f4\u65b0\u4f8b\u884c"),
        },
        "memory": {
            "confirm": ("\u786e\u8ba4\u8bb0\u5fc6",), "correct": ("\u7ea0\u6b63\u8bb0\u5fc6",),
            "archive": ("\u5f52\u6863\u8bb0\u5fc6",), "forget": ("\u5fd8\u8bb0\u8bb0\u5fc6", "\u5220\u9664\u8bb0\u5fc6"),
        },
    }
    NEGATED = ("\u5047\u5982", "\u5982\u679c", "\u600e\u4e48\u505a", "\u4f1a\u600e\u6837", "\u793a\u4f8b", "\u6d4b\u8bd5\u8bf4\u660e", "\u4e0d\u8981\u521b\u5efa", "\u4e0d\u7528\u521b\u5efa")

    def parse(self, text: str, run_id: str, *, timezone_name: str = "Asia/Shanghai", pending: dict | None = None, source_message_id: str = "") -> StructuredCommand | None:
        text = text.strip()
        if pending is not None:
            previous = dict(pending["command"])
            arguments = dict(previous.get("arguments") or {})
            if pending.get("confirmation_state") == "confirmation_required" and text.casefold() in {"\u786e\u8ba4", "\u786e\u8ba4\u6267\u884c", "confirm", "yes"}:
                return self.command(run_id, previous["action"], arguments, text, previous.get("source_message_id", ""), previous.get("idempotency_key", ""))
            arguments.update(self.continuation_arguments(text, pending.get("missing_fields", []), timezone_name))
            if any(str(arguments.get(field) or "").strip() for field in pending.get("missing_fields", [])):
                return self.command(run_id, previous["action"], arguments, text, previous.get("source_message_id", ""), previous.get("idempotency_key", ""))
            return None
        if any(marker in text for marker in self.NEGATED):
            return None
        english = self.parse_english(text, run_id, timezone_name, source_message_id)
        if english:
            return english
        for domain, operations in self.ACTIONS.items():
            for operation, markers in operations.items():
                if any(marker in text for marker in markers):
                    action = f"{domain}.{operation}"
                    if ActionRegistry.supports(action):
                        return self.command(run_id, action, self.arguments(action, text, timezone_name), text, source_message_id)
        return None

    def parse_english(self, text: str, run_id: str, timezone_name: str, source_message_id: str) -> StructuredCommand | None:
        match = re.match(r"^(?:please\s+)?(create|query|update|complete|cancel|delete|snooze|pause|resume|confirm|correct|archive|forget)\s+(task|reminder|routine|memory)\b", text, re.I)
        if not match:
            return None
        operation, domain = match.group(1).lower(), match.group(2).lower()
        action = f"{domain}.{operation}"
        if not ActionRegistry.supports(action):
            return None
        return self.command(run_id, action, self.english_arguments(action, text, timezone_name), text, source_message_id)

    def command(self, run_id: str, action: str, arguments: dict, text: str, source_message_id: str = "", idempotency_key: str = "") -> StructuredCommand:
        normalized = re.sub(r"\s+", " ", text).strip().casefold()
        source = source_message_id or hashlib.sha256(normalized.encode()).hexdigest()
        fingerprint = hashlib.sha256(f"{source}:{action}:{sorted(arguments.items())}".encode()).hexdigest()
        spec = ActionRegistry.get(action)
        return StructuredCommand(f"cmd_{uuid.uuid4().hex[:16]}", run_id, source_message_id=source_message_id, action=action, arguments=arguments, idempotency_key=idempotency_key or fingerprint, risk_level=spec.risk_level if spec else "read_only")

    def continuation_arguments(self, text: str, fields: list[str], timezone_name: str) -> dict:
        values: dict = {}
        for field in fields:
            if field == "scheduled_at":
                value = self.parse_time(text, timezone_name)
            elif field == "id":
                value = self.entity_id(text)
            elif field == "cron":
                value = self.extract_cron(text)
            elif field == "routine_type":
                found = re.search(r"\b(morning_plan|evening_review|weekly_review)\b", text)
                value = found.group(1) if found else ""
            else:
                value = text.strip()
            if value:
                values[field] = value
        return values

    def arguments(self, action: str, text: str, timezone_name: str) -> dict:
        entity_id = self.entity_id(text)
        if action == "reminder.create":
            return {"content": self.strip_markers(text, self.ACTIONS["reminder"]["create"]), "scheduled_at": self.parse_time(text, timezone_name), "timezone": timezone_name, "target_channel": "web"}
        if action == "task.create":
            return {"title": self.strip_markers(text, self.ACTIONS["task"]["create"])}
        if action == "routine.create":
            value = self.strip_markers(text, self.ACTIONS["routine"]["create"])
            return {"name": value, "routine_type": "morning_plan", "cron": self.extract_cron(text), "timezone": timezone_name, "target_channel": "web"}
        if action.endswith((".complete", ".cancel", ".delete", ".update", ".snooze", ".pause", ".resume", ".confirm", ".correct", ".archive", ".forget")):
            result = {"id": entity_id, "scheduled_at": self.parse_time(text, timezone_name)}
            if action == "memory.correct":
                result["replacement_text"] = self.strip_markers(text, self.ACTIONS["memory"]["correct"]).replace(entity_id, "").strip(" ：:，,。")
            if action == "routine.update":
                result["cron"] = self.extract_cron(text)
            return {key: value for key, value in result.items() if value}
        return {}

    def english_arguments(self, action: str, text: str, timezone_name: str) -> dict:
        entity_id = self.entity_id(text)
        tail = re.sub(r"^(?:please\s+)?\w+\s+\w+\s*", "", text, flags=re.I).strip()
        if action == "task.create": return {"title": tail}
        if action == "reminder.create": return {"content": tail, "scheduled_at": self.parse_time(text, timezone_name), "timezone": timezone_name, "target_channel": "web"}
        if action == "routine.create":
            routine_type = next((value for value in ("morning_plan", "evening_review", "weekly_review") if value in text), "morning_plan")
            return {"name": re.sub(r"\s+cron\s*[:=].*$", "", tail, flags=re.I).strip(), "routine_type": routine_type, "cron": self.extract_cron(text), "timezone": timezone_name, "target_channel": "web"}
        args = {"id": entity_id}
        if action == "reminder.snooze": args["scheduled_at"] = self.parse_time(text, timezone_name)
        if action == "memory.correct": args["replacement_text"] = re.sub(rf"\b{re.escape(entity_id)}\b", "", tail).strip()
        if action == "task.update": args["title"] = self.extract_named_value(text, "title")
        if action == "reminder.update": args["content"] = self.extract_named_value(text, "content")
        if action == "routine.update":
            args["name"] = self.extract_named_value(text, "name")
            args["cron"] = self.extract_cron(text)
        return {key: value for key, value in args.items() if value}

    def extract_named_value(self, text: str, field: str) -> str:
        match = re.search(rf"\b{field}\s*[:=]\s*([^,，\n]+)", text, re.I)
        return match.group(1).strip() if match else ""

    def extract_cron(self, text: str) -> str:
        match = re.search(r"\bcron\s*[:=]\s*([\d*/?,\-\s]+)", text, re.I)
        return match.group(1).strip() if match else ""

    def parse_time(self, text: str, timezone_name: str) -> str:
        zone = ZoneInfo(timezone_name); now = datetime.now(zone)
        iso = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})", text)
        if iso: return iso.group(0)
        clock = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))\b", text, re.I)
        if clock:
            day = now.date() + timedelta(days=1 if "tomorrow" in text.lower() or "\u660e\u5929" in text else 0)
            return datetime.combine(day, datetime.min.time(), tzinfo=zone).replace(hour=int(clock.group(1)), minute=int(clock.group(2) or 0)).isoformat()
        day = now.date()
        if "\u540e\u5929" in text: day += timedelta(days=2)
        elif "\u660e\u5929" in text: day += timedelta(days=1)
        elif "\u4eca\u5929" not in text and not re.search(r"(?:\u4e0a\u5348|\u4e0b\u5348|\u665a\u4e0a|\u4e2d\u5348|\d{1,2}\u70b9|\d{1,2}\u65f6)", text): return ""
        normalized = self.normalize_chinese_clock(text)
        match = re.search(r"(\d{1,2})(?:[:\uff1a](\d{2}))?\s*(?:\u70b9|\u65f6)?", normalized)
        if not match: return ""
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if ("\u4e0b\u5348" in text or "\u665a\u4e0a" in text) and hour < 12: hour += 12
        if "\u4e2d\u5348" in text and hour < 11: hour += 12
        try: return datetime.combine(day, datetime.min.time(), tzinfo=zone).replace(hour=hour, minute=minute).isoformat()
        except ValueError: return ""

    def normalize_chinese_clock(self, text: str) -> str:
        numbers = {"\u96f6": 0, "\u4e00": 1, "\u4e8c": 2, "\u4e24": 2, "\u4e09": 3, "\u56db": 4, "\u4e94": 5, "\u516d": 6, "\u4e03": 7, "\u516b": 8, "\u4e5d": 9, "\u5341": 10}
        match = re.search(r"([\u96f6\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)(?:\u70b9|\u65f6)", text)
        if not match: return text
        value = match.group(1)
        if value == "\u5341": hour = 10
        elif value.startswith("\u5341"): hour = 10 + numbers.get(value[1:], 0)
        elif value.endswith("\u5341"): hour = numbers.get(value[0], 0) * 10
        elif "\u5341" in value: hour = numbers.get(value[0], 0) * 10 + numbers.get(value[-1], 0)
        else: hour = numbers.get(value, -1)
        return text.replace(match.group(0), f"{hour}\u70b9") if hour >= 0 else text
        return ""

    def entity_id(self, text: str) -> str:
        match = re.search(r"\b(?:ptask|rem|routine|mem)_[a-f0-9]{6,}\b", text)
        return match.group(0) if match else ""

    def strip_markers(self, text: str, markers: tuple[str, ...]) -> str:
        value = text
        for marker in markers: value = value.replace(marker, "")
        return re.sub(r"\s+", " ", value).strip(" ：:，,。")
