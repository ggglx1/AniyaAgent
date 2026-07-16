from __future__ import annotations


class MemoryPolicy:
    sensitive_markers = ("密码", "身份证", "银行卡", "api key", "secret", "token", "访问令牌", "精确位置", "经纬度", "诊断", "病历")

    def decide(self, candidate: dict) -> str:
        text = candidate["content"].lower()
        if any(marker in text for marker in self.sensitive_markers):
            return "discard"
        if candidate["memory_type"] == "profile_fact":
            return "route_profile"
        if candidate["memory_type"] in {"task", "reminder"}:
            return "route_personal_state"
        return "write_active" if candidate["explicit"] else "write_pending"
