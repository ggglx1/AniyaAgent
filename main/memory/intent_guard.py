from __future__ import annotations

import hashlib
import re


class IntentGuard:
    """Conservative gate for all automatic personal-state writes."""
    reject_terms = ("不要", "别", "无需", "取消", "不存在", "没有创建", "失败", "错误", "假设", "例如", "测试", "模拟", "如果", "引用")

    def decision(self, text: str, *, source_role: str = "user", has_complete_time: bool = True, require_action: bool = False) -> str:
        clean = text.strip()
        if source_role != "user" or not clean: return "rejected"
        if any(term in clean for term in self.reject_terms): return "rejected"
        if require_action and not self.has_action(clean): return "pending"
        if not has_complete_time: return "pending"
        return "confirmed"

    def has_action(self, text: str) -> bool:
        return bool(re.search(r"(完成|整理|购买|联系|提交|安排|提醒|创建|处理|查看|学习|写|读|改|发)", text))

    def idempotency_key(self, message_id: str, extractor_version: str, kind: str, text: str) -> str:
        normalized = re.sub(r"\s+", "", text).lower()
        return hashlib.sha256(f"{message_id}|{extractor_version}|{kind}|{normalized}".encode()).hexdigest()
