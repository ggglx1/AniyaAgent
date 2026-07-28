from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextPolicy:
    mode: str
    include_memory: bool = False
    include_daily: bool = False
    include_personal_state: bool = False
    include_attachments: bool = False
    include_repository: bool = False
    max_tokens: int = 3_000

    @classmethod
    def for_run_type(cls, run_type: str) -> "ContextPolicy":
        policies = {
            "direct_conversation": cls("assistant", True, True, False, True, max_tokens=1_500),
            "structured_action": cls("assistant", False, False, True, False, max_tokens=500),
            "deliberative_agent": cls("assistant", True, True, True, True, max_tokens=4_500),
            "qa": cls("qa", False, False, False, True, max_tokens=1_200),
            "coding": cls("coding", False, False, False, True, True, max_tokens=4_000),
            "proactive": cls("assistant", False, False, False, False, max_tokens=400),
        }
        return policies[run_type]
