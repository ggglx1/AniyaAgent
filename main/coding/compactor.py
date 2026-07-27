from __future__ import annotations


class CodingTurnCompactor:
    """Preserves tool transactions while replacing oversized results with artifact references."""
    def __init__(self, max_result_chars: int = 16_000): self.max_result_chars = max_result_chars

    def compact(self, messages: list, artifacts) -> list:
        compacted = []
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if message.get("role") != "user" or not isinstance(content, list):
                compacted.append(message); continue
            blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and len(str(block.get("content") or "")) > self.max_result_chars:
                    artifact = artifacts.save(block["content"])
                    blocks.append({**block, "content": f"[Tool result archived: {artifact['path']}; sha256={artifact['sha256']}; chars={artifact['chars']}]"})
                else: blocks.append(block)
            compacted.append({**message, "content": blocks})
        return compacted
