from __future__ import annotations

from .models import RouteDecision, RunRequest


class RunRouter:
    """Cheap deterministic routing. Ambiguous writes never become a free-form tool loop."""
    action_words = {"task": ("创建任务", "添加任务", "待办", "完成任务", "取消任务"), "reminder": ("提醒我", "创建提醒", "稍后提醒", "取消提醒"), "routine": ("例行", "routine", "暂停提醒"), "memory": ("记住这个", "忘记", "纠正记忆", "归档记忆")}
    unsafe = ("不要", "别", "假设", "测试", "模拟", "如果", "失败", "错误", "引用")

    def route(self, request: RunRequest) -> RouteDecision:
        if request.mode == "qa": return RouteDecision("qa", "qa", "knowledge_question", 1.0, "explicit QA mode")
        if request.mode == "coding": return RouteDecision("coding", "coding", "coding_task", 1.0, "explicit Coding mode")
        if request.metadata.get("proactive_event"): return RouteDecision("assistant", "proactive", "proactive_event", 1.0, "scheduler event")
        text = request.text.strip()
        if any(word in text for word in self.unsafe): return RouteDecision("assistant", "direct_conversation", "conversation", .95, "unsafe/test language prevents automatic action")
        for intent, words in self.action_words.items():
            if any(word.lower() in text.lower() for word in words):
                missing = ["time"] if intent == "reminder" and not self.has_time(text) else []
                return RouteDecision("assistant", "structured_action", intent, .9, "deterministic action rule", requires_confirmation=bool(missing), missing_fields=missing)
        if self.is_complex(text, request.metadata): return RouteDecision("assistant", "deliberative_agent", "open_task", .65, "multi-step or capability request", required_capabilities=["local_tools"])
        return RouteDecision("assistant", "direct_conversation", "conversation", .8, "simple conversation fast path")

    def has_time(self, text: str) -> bool:
        import re
        return bool(re.search(r"\d{1,2}[:：]\d{2}|\d{1,2}点|上午|下午|晚上|中午|T\d{2}:\d{2}", text))

    def is_complex(self, text: str, metadata: dict) -> bool:
        return bool(metadata.get("attachment_ids") or any(word in text for word in ("分析", "规划", "比较", "查找", "文件", "帮我完成", "一步一步", "多个")))
