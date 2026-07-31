from __future__ import annotations

import uuid
from dataclasses import replace

from main.actions.models import ActionResolution
from main.actions.registry import ActionRegistry

from .entity_resolver import EntityResolver
from .guard import ActionIntentGuard
from .llm_recognizer import LlmIntentRecognizer
from .rule_recognizer import RuleIntentRecognizer
from .temporal_resolver import TemporalResolver


class IntentResolutionService:
    """Single safe boundary between natural language and deterministic actions."""

    policy_version = "intent-policy-v1"

    def __init__(self, application):
        self.app = application
        self.guard = ActionIntentGuard(); self.rule = RuleIntentRecognizer()
        self.temporal = TemporalResolver(); self.entities = EntityResolver(application)
        self.llm = LlmIntentRecognizer(application, getattr(application, "intent_llm_recognizer", None))

    def resolve(self, request, pending: dict | None = None, cancellation_token=None) -> ActionResolution:
        source_message_id = str(request.metadata.get("client_message_id") or request.metadata.get("source_message_id") or "")
        timezone_name = str(self.app.runtime.profile_store.get().get("timezone") or "Asia/Shanghai")
        if pending is None:
            guard = self.guard.inspect(request.text)
            if guard.blocked:
                return self.result("non_action" if not guard.ambiguous else "ambiguous", policy_reason=guard.reason, required_user_response="Clarify the action you want to perform.")
        else:
            guard = None
        candidate, command = self.rule.recognize(request.text, request.run_id, timezone_name=timezone_name, source_message_id=source_message_id, pending=pending)
        if candidate is None and pending is None:
            candidate = self.llm.recognize(request.text, request.run_id, source_message_id, cancellation_token=cancellation_token)
            command = self.command_from_candidate(candidate, request) if candidate else None
        if candidate is None or command is None:
            return self.result("non_action", policy_reason="no_reliable_action_candidate")
        if guard is not None:
            candidate = replace(candidate, negated=guard.negated, hypothetical=guard.hypothetical, quoted=guard.quoted, capability_question=guard.capability_question)
        if candidate.ambiguities:
            return self.result("ambiguous", candidate=candidate, policy_reason="recognizer_ambiguity", required_user_response="Please clarify which action you intend.")
        spec = ActionRegistry.get(command.action)
        if spec is None:
            return self.result("blocked", candidate=candidate, policy_reason="unsupported_action")
        confidence = {"low": 0, "medium": 1, "high": 2}
        if confidence.get(candidate.language_confidence, 0) < confidence.get(spec.minimum_confidence, 2):
            return self.result("ambiguous", candidate=candidate, command=command, policy_reason="confidence_below_execution_threshold", required_user_response="Please restate the action with an explicit target and parameters.")
        schema_error = ActionRegistry.validate_arguments(command.action, command.arguments)
        if schema_error:
            return self.result("blocked", candidate=candidate, command=command, policy_reason=schema_error, required_user_response="Please use only supported action fields.")
        missing = ActionRegistry.missing(command.action, command.arguments)
        if missing == ["id"]:
            entity = self.entities.resolve(command.action, command.arguments, request.text)
            if entity.ambiguity == "entity_selection_required":
                return self.result("ambiguous", candidate=candidate, command=command, missing_fields=missing, ambiguous_entities=list(entity.candidates), policy_reason=entity.ambiguity, required_user_response="Choose one target ID from the candidate list.")
        if missing:
            return self.result("missing_fields", candidate=candidate, command=command, missing_fields=missing, policy_reason="required_fields_missing", required_user_response="Please provide the missing fields.")
        if command.action in {"reminder.create", "reminder.snooze"} or (command.action == "reminder.update" and "scheduled_at" in command.arguments):
            temporal = self.temporal.resolve(request.text, timezone_name, str(command.arguments.get("scheduled_at") or ""))
            if temporal.issue:
                field = "scheduled_at" if temporal.issue in {"time_missing", "time_incomplete"} else ""
                return self.result("missing_fields" if field else "ambiguous", candidate=candidate, command=command, missing_fields=[field] if field else [], temporal_issue=temporal.issue, policy_reason=temporal.issue, required_user_response="Please provide an exact future date, time, and timezone.")
            command.arguments["scheduled_at"] = temporal.value
        allowed_ids = set(command.result.get("allowed_entity_ids") or [])
        if allowed_ids and str(command.arguments.get("id") or "") not in allowed_ids:
            return self.result("ambiguous", candidate=candidate, command=command, ambiguous_entities=sorted(allowed_ids), policy_reason="entity_selection_outside_candidates", required_user_response="Choose one of the IDs shown in the pending selection.")
        entity = self.entities.resolve(command.action, command.arguments, request.text)
        if entity.ambiguity:
            return self.result("ambiguous", candidate=candidate, command=command, ambiguous_entities=[entity.ambiguity], policy_reason=entity.ambiguity, required_user_response="Please provide the exact ID of the target item.")
        # Only a confirmation-stage continuation was already previewed. Missing-field
        # continuations must still show the resolved action before writing state.
        if pending is not None and pending.get("confirmation_state") in {"preview_required", "confirmation_required"}:
            return self.result("ready", candidate=candidate, command=command, policy_reason="pending_action_confirmed")
        if spec.preview_required:
            status = "confirmation_required" if spec.requires_confirmation else "preview_required"
            return self.result(status, candidate=candidate, command=command, policy_reason=spec.execution_policy, required_user_response="Review the action preview and confirm, modify, or cancel.")
        return self.result("ready", candidate=candidate, command=command, policy_reason="read_only_action")

    def command_from_candidate(self, candidate, request):
        if candidate is None:
            return None
        # The compatibility parser owns stable idempotency construction.
        return self.rule.parser.command(request.run_id, candidate.intent, dict(candidate.arguments), request.text, str(request.metadata.get("client_message_id") or ""))

    def result(self, status: str, *, candidate=None, command=None, missing_fields=None, ambiguous_entities=None, temporal_issue="", policy_reason="", required_user_response="") -> ActionResolution:
        return ActionResolution(
            resolution_id=f"res_{uuid.uuid4().hex[:16]}", candidate_id=candidate.candidate_id if candidate else "", status=status,
            validated_action=command, missing_fields=list(missing_fields or []), ambiguous_entities=list(ambiguous_entities or []),
            temporal_issue=temporal_issue, policy_reason=policy_reason, required_user_response=required_user_response,
            candidate=candidate, execution_policy_version=self.policy_version,
        )
