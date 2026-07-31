from __future__ import annotations

import uuid

from main.actions.models import ActionCandidate
from main.actions.parser import StructuredCommandParser


class RuleIntentRecognizer:
    """Compatibility wrapper around the former high-precision parser."""

    version = "rule-v2"

    def __init__(self): self.parser = StructuredCommandParser()

    def recognize(self, text: str, run_id: str, *, timezone_name: str, source_message_id: str = "", pending: dict | None = None) -> tuple[ActionCandidate | None, object | None]:
        command = self.parser.parse(text, run_id, timezone_name=timezone_name, source_message_id=source_message_id, pending=pending)
        if command is None:
            return None, None
        markers = [marker for marker in self.parser.ACTIONS.get(command.action.split(".")[0], {}).get(command.action.split(".")[1], ()) if marker in text]
        candidate = ActionCandidate(
            candidate_id=f"cand_{uuid.uuid4().hex[:16]}", run_id=run_id, source_message_id=source_message_id,
            intent=command.action, arguments=dict(command.arguments), evidence_spans=markers or [text[:160]],
            recognizer=self.version, language_confidence="high",
        )
        return candidate, command
