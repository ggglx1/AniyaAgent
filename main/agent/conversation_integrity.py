from __future__ import annotations

import copy
from dataclasses import dataclass, field


RECOVERY_RESULT = (
    "Tool execution outcome is unavailable because conversation recovery detected an "
    "incomplete tool transaction. Do not assume success and do not automatically repeat side effects."
)
VALID_ROLES = {"user", "assistant", "system", "tool"}


@dataclass
class ConversationUnit:
    kind: str
    messages: list[dict]
    tool_use_ids: set[str] = field(default_factory=set)
    tool_result_ids: set[str] = field(default_factory=set)
    valid: bool = True
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class IntegrityReport:
    valid: bool
    errors: list[str] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)


class ConversationIntegrityValidator:
    """Single source of truth for tool transaction parsing, validation and recovery."""

    def parse(self, messages: list) -> list[ConversationUnit]:
        units: list[ConversationUnit] = []
        index = 0
        seen_ids: set[str] = set()
        while index < len(messages):
            message = messages[index]
            if not isinstance(message, dict):
                units.append(ConversationUnit("plain", [], valid=False, diagnostics=[f"message[{index}] is not an object"])); index += 1; continue
            role = message.get("role")
            if role not in VALID_ROLES:
                units.append(ConversationUnit("plain", [message], valid=False, diagnostics=[f"message[{index}] has unsupported role"])); index += 1; continue
            uses = self.tool_uses(message)
            results = self.tool_results(message)
            if results:
                units.append(ConversationUnit("plain", [message], tool_result_ids=set(results), valid=False, diagnostics=[f"orphan tool_result(s) at message[{index}]"])); index += 1; continue
            if not uses:
                units.append(ConversationUnit("plain", [message])); index += 1; continue
            ids = list(uses)
            diagnostics = []
            if role != "assistant": diagnostics.append(f"tool_use outside assistant at message[{index}]")
            if any(len(blocks) != 1 for blocks in uses.values()) or any(not item for item in ids): diagnostics.append(f"missing or duplicate tool_use id at message[{index}]")
            if any(item in seen_ids for item in ids if item): diagnostics.append(f"tool_use id reused at message[{index}]")
            seen_ids.update(item for item in ids if item)
            next_message = messages[index + 1] if index + 1 < len(messages) else None
            next_results = self.tool_results(next_message) if isinstance(next_message, dict) else {}
            expected = set(ids)
            actual = set(next_results)
            if not isinstance(next_message, dict) or next_message.get("role") != "user": diagnostics.append(f"tool_use at message[{index}] is not followed by a user tool_result message")
            elif actual != expected or any(len(items) != 1 for items in next_results.values()): diagnostics.append(f"tool result set does not exactly match tool_use set at message[{index}]")
            valid = not diagnostics
            unit_messages = [message, next_message] if isinstance(next_message, dict) and next_message.get("role") == "user" and next_results else [message]
            units.append(ConversationUnit("tool_transaction", unit_messages, expected, actual, valid, diagnostics))
            index += 2 if len(unit_messages) == 2 else 1
        return units

    def validate(self, messages: list) -> IntegrityReport:
        errors = [diagnostic for unit in self.parse(messages) if not unit.valid for diagnostic in unit.diagnostics]
        return IntegrityReport(not errors, errors)

    def repair(self, messages: list) -> tuple[list, IntegrityReport]:
        """Repair each transaction in place and quarantine everything not safely attributable."""
        source = copy.deepcopy(messages); repaired: list[dict] = []; quarantine: list[dict] = []; seen: set[str] = set(); index = 0
        while index < len(source):
            message = source[index]
            if not isinstance(message, dict) or message.get("role") not in VALID_ROLES:
                quarantine.append({"index": index, "reason": "invalid message", "message": message}); index += 1; continue
            cleaned, invalid_blocks = self.clean_tool_uses(message, seen)
            quarantine.extend({"index": index, "reason": reason, "block": block} for reason, block in invalid_blocks)
            if self.empty_message(cleaned): index += 1; continue
            uses = self.tool_uses(cleaned)
            if not uses:
                if self.tool_results(cleaned):
                    quarantine.append({"index": index, "reason": "orphan tool_result message", "message": cleaned}); index += 1; continue
                repaired.append(cleaned); index += 1; continue
            expected = set(uses)
            next_message = source[index + 1] if index + 1 < len(source) else None
            legitimate_results: list[object] = []; consumed_next = False
            if isinstance(next_message, dict) and next_message.get("role") == "user":
                for result_id, blocks in self.tool_results(next_message).items():
                    for offset, block in enumerate(blocks):
                        if result_id in expected and result_id not in {self.value(item, "tool_use_id", "") for item in legitimate_results} and offset == 0:
                            legitimate_results.append(block)
                        else:
                            quarantine.append({"index": index + 1, "reason": "orphan or duplicate tool_result", "block": block})
                if self.tool_results(next_message): consumed_next = True
            present = {str(self.value(block, "tool_use_id", "")) for block in legitimate_results}
            for tool_id in expected - present:
                legitimate_results.append({"type": "tool_result", "tool_use_id": tool_id, "content": RECOVERY_RESULT, "is_error": True})
            repaired.append(cleaned)
            repaired.append({"role": "user", "content": legitimate_results})
            if consumed_next:
                remainder = self.without_tool_results(next_message)
                if not self.empty_message(remainder):
                    repaired.append(remainder)
                index += 2
            else:
                index += 1
        report = self.validate(repaired); report.quarantined = quarantine
        return repaired, report

    def units(self, messages: list) -> list[ConversationUnit]:
        units = self.parse(messages)
        if any(not unit.valid for unit in units):
            raise ValueError("Conversation units requested from an invalid history")
        return units

    def flatten(self, units: list[ConversationUnit | list[dict]]) -> list[dict]:
        return [message for unit in units for message in (unit.messages if isinstance(unit, ConversationUnit) else unit)]

    def tool_uses(self, message: object) -> dict[str, list[object]]:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list): return {}
        items: dict[str, list[object]] = {}
        for block in message["content"]:
            if self.value(block, "type") == "tool_use": items.setdefault(str(self.value(block, "id", "")), []).append(block)
        return items

    def tool_results(self, message: object) -> dict[str, list[object]]:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list): return {}
        items: dict[str, list[object]] = {}
        for block in message["content"]:
            if self.value(block, "type") == "tool_result": items.setdefault(str(self.value(block, "tool_use_id", "")), []).append(block)
        return items

    def clean_tool_uses(self, message: dict, seen: set[str]) -> tuple[dict, list[tuple[str, object]]]:
        if message.get("role") != "assistant" or not isinstance(message.get("content"), list): return message, []
        kept, invalid = [], []
        for block in message["content"]:
            if self.value(block, "type") != "tool_use": kept.append(block); continue
            tool_id = str(self.value(block, "id", ""))
            if not tool_id or tool_id in seen: invalid.append(("missing or duplicate tool_use", block)); continue
            seen.add(tool_id); kept.append(block)
        return {**message, "content": kept}, invalid

    def without_tool_results(self, message: dict) -> dict:
        content = message.get("content")
        if not isinstance(content, list): return message
        return {**message, "content": [block for block in content if self.value(block, "type") != "tool_result"]}

    def empty_message(self, message: dict) -> bool:
        content = message.get("content")
        return content is None or content == "" or content == []

    def value(self, block: object, key: str, default=None):
        return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)
