from __future__ import annotations

import json
import re

from main.actions.models import ActionCandidate
from main.actions.registry import ActionRegistry


class LlmIntentRecognizer:
    """Optional, schema-bound fallback. It never receives tools or domain services."""

    version = "llm-schema-v1"

    action_hints = ("\u4efb\u52a1", "\u63d0\u9192", "\u4f8b\u884c", "\u8bb0\u5fc6", "task", "remind", "routine", "memory")

    def __init__(self, application, callable_recognizer=None):
        self.application = application; self.callable_recognizer = callable_recognizer

    def recognize(self, text: str, run_id: str, source_message_id: str = "", cancellation_token=None) -> ActionCandidate | None:
        if not any(hint in text.casefold() for hint in self.action_hints):
            return None
        try:
            raw = self.callable_recognizer(text, sorted(ActionRegistry._BY_NAME)) if self.callable_recognizer else self.gateway_candidate(text, run_id, cancellation_token)
        except Exception:
            return None
        if not isinstance(raw, dict) or not ActionRegistry.supports(str(raw.get("intent") or "")):
            return None
        evidence = [str(item) for item in raw.get("evidence_spans", []) if isinstance(item, str) and item in text]
        if not evidence or raw.get("multiple_candidates"):
            return None
        return ActionCandidate(
            candidate_id=str(raw.get("candidate_id") or f"llm_{run_id}"), run_id=run_id, source_message_id=source_message_id,
            intent=str(raw["intent"]), arguments=dict(raw.get("arguments") or {}), evidence_spans=evidence,
            recognizer=self.version, language_confidence="medium", ambiguities=list(raw.get("ambiguities") or []),
        )

    def gateway_candidate(self, text: str, run_id: str, cancellation_token=None) -> dict:
        """One tool-free JSON-only request; local validation remains authoritative."""
        runtime = self.application.runtime
        actions = sorted(ActionRegistry._BY_NAME)
        system = (
            "Classify whether the user requests one supported personal-assistant action. "
            "Return JSON only: {intent,arguments,evidence_spans,ambiguities,multiple_candidates}. "
            f"intent must be null or one of: {actions}. Do not execute anything."
        )
        response = runtime.llm_gateway.messages.create(
            task_type="structured_repair", model=runtime.MODEL, max_tokens=400, system=system,
            messages=[{"role": "user", "content": text}], tools=[], cancellation_token=cancellation_token,
            run_context={"run_id": run_id, "executor": "intent_recognizer", "route": "intent_recognizer"},
        )
        content = runtime.extract_text(getattr(response, "content", []) or []).strip()
        match = re.search(r"\{.*\}", content, re.S)
        return json.loads(match.group(0) if match else content)
