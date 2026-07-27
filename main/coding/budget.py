from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass
class CodingBudget:
    max_model_requests: int = 5
    max_total_input_tokens: int = 40_000
    max_tool_result_tokens: int = 20_000
    max_wall_time: float = 600.0
    max_repeated_tool_signature: int = 3
    requests: int = 0
    input_tokens: int = 0
    tool_result_tokens: int = 0
    started_at: float = 0.0
    repeated_signature: str = ""
    repeated_count: int = 0

    def __post_init__(self):
        self.started_at = time.monotonic()

    def allow_request(self, messages: list) -> bool:
        estimated = max(1, len(str(messages)) // 4)
        return self.requests < self.max_model_requests and self.input_tokens + estimated <= self.max_total_input_tokens and time.monotonic() - self.started_at < self.max_wall_time

    def record_request(self, messages: list, usage: dict | None = None):
        self.requests += 1
        actual = int((usage or {}).get("input_tokens") or 0)
        self.input_tokens += actual or max(1, len(str(messages)) // 4)

    def record_tool_result(self, value: object) -> bool:
        self.tool_result_tokens += max(1, len(str(value)) // 4)
        return self.tool_result_tokens <= self.max_tool_result_tokens

    def record_tool_signature(self, signature: str) -> bool:
        if signature == self.repeated_signature: self.repeated_count += 1
        else: self.repeated_signature, self.repeated_count = signature, 1
        return self.repeated_count <= self.max_repeated_tool_signature
