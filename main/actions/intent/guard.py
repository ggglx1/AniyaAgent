from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardResult:
    blocked: bool = False
    reason: str = ""
    negated: bool = False
    hypothetical: bool = False
    quoted: bool = False
    capability_question: bool = False
    ambiguous: bool = False


class ActionIntentGuard:
    """Reject wording that mentions an action without requesting its execution."""

    capability_prefixes = ("\u4f60\u80fd", "\u4f60\u4f1a", "can you", "how can", "what can")
    hypothetical_markers = ("\u5982\u679c", "\u5047\u5982", "\u8981\u662f", "\u4e3e\u4e2a\u4f8b\u5b50", "\u600e\u4e48\u8bf4", "if ", "example")
    test_markers = ("\u6d4b\u8bd5\u4e00\u4e0b", "\u6d4b\u8bd5\u8bf4\u660e", "\u793a\u4f8b", "test phrase")
    negated_markers = ("\u4e0d\u8981", "\u4e0d\u7528", "\u522b", "do not", "don't")

    def inspect(self, text: str) -> GuardResult:
        value = text.strip(); lowered = value.casefold()
        capability = lowered.startswith(self.capability_prefixes) or lowered.endswith("?") and any(word in value for word in ("\u63d0\u9192", "\u4efb\u52a1", "\u8bb0\u5fc6"))
        hypothetical = any(marker in lowered for marker in self.hypothetical_markers)
        reported_speech = any(mark in value for mark in ("\u4ed6\u8bf4", "\u5979\u8bf4", "\u8bf4\u9053", "quoted:"))
        quote_pair = ("\u201c" in value and "\u201d" in value) or value.strip().startswith(("\"", "'"))
        quoted = reported_speech or (quote_pair and any(marker in lowered for marker in self.hypothetical_markers))
        test = any(marker in lowered for marker in self.test_markers)
        negated = any(marker in lowered for marker in self.negated_markers)
        explicit_cancel = lowered.startswith(("\u53d6\u6d88\u4efb\u52a1", "\u53d6\u6d88\u63d0\u9192", "cancel task", "cancel reminder"))
        cancel_negated = any(marker in lowered for marker in ("\u4e0d\u8981\u53d6\u6d88", "\u4e0d\u7528\u53d6\u6d88", "do not cancel", "don't cancel"))
        if explicit_cancel and not cancel_negated:
            negated = False
        if capability: return GuardResult(True, "capability_question", capability_question=True)
        if hypothetical: return GuardResult(True, "hypothetical_or_example", hypothetical=True)
        if quoted: return GuardResult(True, "quoted_or_reported_speech", quoted=True)
        if test: return GuardResult(True, "test_or_example_text")
        if negated: return GuardResult(True, "negated_action", negated=True)
        # Multiple imperative clauses cannot be made atomic without a declared transaction.
        connectors = sum(value.count(item) for item in ("\u987a\u4fbf", "\u540c\u65f6", "\u5e76\u4e14", " and "))
        if connectors and sum(value.count(item) for item in ("\u521b\u5efa", "\u53d6\u6d88", "\u5220\u9664", "\u5b8c\u6210", "\u63d0\u9192")) > 1:
            return GuardResult(True, "multiple_actions_need_clarification", ambiguous=True)
        return GuardResult()
