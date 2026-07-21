from __future__ import annotations

import copy
from dataclasses import dataclass, field


RECOVERY_RESULT = (
    "Tool execution outcome is unavailable because conversation recovery detected "
    "an incomplete tool transaction. Do not assume success and do not automatically "
    "repeat side effects."
)


@dataclass
class IntegrityReport:
    valid: bool
    errors: list[str] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)


class ConversationIntegrityValidator:
    """Provider-neutral structural guard for message and tool-result history."""

    def validate(self, messages: list) -> IntegrityReport:
        uses: dict[str, int] = {}
        results: dict[str, list[int]] = {}
        errors: list[str] = []
        for index, message in enumerate(messages):
            role = message.get("role") if isinstance(message, dict) else None
            if role not in {"user", "assistant", "system", "tool"}:
                errors.append(f"message[{index}] has unsupported role")
                continue
            content = message.get("content", "") if isinstance(message, dict) else ""
            if role == "assistant" and isinstance(content, list):
                for block in content:
                    if self.value(block, "type") == "tool_use":
                        tool_id = str(self.value(block, "id", ""))
                        if not tool_id or tool_id in uses:
                            errors.append(f"duplicate or missing tool_use id at message[{index}]")
                        else:
                            uses[tool_id] = index
            if isinstance(content, list):
                for block in content:
                    if self.value(block, "type") != "tool_result":
                        continue
                    tool_id = str(self.value(block, "tool_use_id", ""))
                    results.setdefault(tool_id, []).append(index)
                    if tool_id not in uses:
                        errors.append(f"orphan tool_result {tool_id} at message[{index}]")
                    elif uses[tool_id] >= index:
                        errors.append(f"tool_result before tool_use {tool_id}")
        for tool_id in uses:
            count = len(results.get(tool_id, []))
            if count != 1:
                errors.append(f"tool transaction {tool_id} has {count} results")
        return IntegrityReport(not errors, errors)

    def repair(self, messages: list) -> tuple[list, IntegrityReport]:
        """Keep the first legal result and make incomplete calls explicit and safe."""
        working = copy.deepcopy(messages)
        uses: dict[str, tuple[int, object]] = {}
        seen_results: set[str] = set()
        quarantine: list[dict] = []
        repaired: list[dict] = []
        pending: list[str] = []
        for index, message in enumerate(working):
            if not isinstance(message, dict):
                quarantine.append({"index": index, "reason": "non-object message", "message": message}); continue
            content = message.get("content", "")
            if message.get("role") == "assistant" and isinstance(content, list):
                for block in content:
                    if self.value(block, "type") == "tool_use":
                        tool_id = str(self.value(block, "id", ""))
                        if not tool_id or tool_id in uses:
                            quarantine.append({"index": index, "reason": "duplicate tool_use", "message": message}); continue
                        uses[tool_id] = (len(repaired), block); pending.append(tool_id)
            if isinstance(content, list):
                kept = []
                for block in content:
                    if self.value(block, "type") != "tool_result": kept.append(block); continue
                    tool_id = str(self.value(block, "tool_use_id", ""))
                    if tool_id not in uses or tool_id in seen_results:
                        quarantine.append({"index": index, "reason": "orphan or duplicate tool_result", "block": block}); continue
                    seen_results.add(tool_id); kept.append(block)
                if len(kept) != len(content):
                    message = {**message, "content": kept}
            repaired.append(message)
        missing = [tool_id for tool_id in pending if tool_id not in seen_results]
        if missing:
            repaired.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": RECOVERY_RESULT, "is_error": True} for tool_id in missing]})
        report = self.validate(repaired); report.quarantined = quarantine
        return repaired, report

    def units(self, messages: list) -> list[list[dict]]:
        """Return message units; assistant tool uses and their complete result turn stay together."""
        units: list[list[dict]] = []; index = 0
        while index < len(messages):
            message = messages[index]
            use_ids = self.tool_use_ids(message)
            if use_ids and index + 1 < len(messages) and self.result_ids(messages[index + 1]) >= use_ids:
                units.append([message, messages[index + 1]]); index += 2
            else:
                units.append([message]); index += 1
        return units

    def flatten(self, units: list[list[dict]]) -> list[dict]:
        return [message for unit in units for message in unit]

    def tool_use_ids(self, message: object) -> set[str]:
        if not isinstance(message, dict) or message.get("role") != "assistant" or not isinstance(message.get("content"), list): return set()
        return {str(self.value(block, "id", "")) for block in message["content"] if self.value(block, "type") == "tool_use" and self.value(block, "id", "")}

    def result_ids(self, message: object) -> set[str]:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list): return set()
        return {str(self.value(block, "tool_use_id", "")) for block in message["content"] if self.value(block, "type") == "tool_result" and self.value(block, "tool_use_id", "")}

    def value(self, block: object, key: str, default=None):
        return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)
