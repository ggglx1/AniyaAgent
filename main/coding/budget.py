from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CodingBudget:
    max_model_requests: int = 5
    max_history_chars: int = 160_000
    max_tool_result_chars: int = 80_000
    requests: int = 0
    tool_result_chars: int = 0
    def allow_request(self, messages: list) -> bool:
        return self.requests < self.max_model_requests and len(str(messages)) <= self.max_history_chars
    def record_request(self): self.requests += 1
    def record_tool_result(self, value: object) -> bool:
        self.tool_result_chars += len(str(value)); return self.tool_result_chars <= self.max_tool_result_chars
