from __future__ import annotations

from dataclasses import dataclass, field

from main.llm.usage import estimate_tokens
from .context_policy import ContextPolicy


@dataclass
class ContextBlock:
    kind: str
    content: object
    source_ids: list[str] = field(default_factory=list)
    priority: int = 0
    estimated_tokens: int = 0
    trimmed_reason: str = ""
    score: float = 0.0


@dataclass
class BuiltContext:
    blocks: list[ContextBlock] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(str(block.content) for block in self.blocks if isinstance(block.content, str) and block.content)

    @property
    def source_ids(self) -> list[str]:
        return [source for block in self.blocks for source in block.source_ids]

    def metadata(self) -> dict:
        return {"blocks": [{"kind": b.kind, "source_ids": b.source_ids, "estimated_tokens": b.estimated_tokens, "trimmed_reason": b.trimmed_reason, "score": b.score} for b in self.blocks], "estimated_tokens": sum(item.estimated_tokens for item in self.blocks)}


class ContextBuilder:
    """The only runtime context assembler. Complete facts remain in SQLite, not prompts."""

    def __init__(self, application): self.app = application

    def build(self, request, policy: ContextPolicy) -> BuiltContext:
        blocks: list[ContextBlock] = []
        remaining = policy.max_tokens
        seen_sources: set[str] = set()

        def add(kind: str, content: str, source_ids: list[str] | None = None, priority: int = 0, score: float = 0.0):
            nonlocal remaining
            source_ids = [item for item in (source_ids or []) if item and item not in seen_sources]
            if not content or remaining <= 0:
                return
            tokens = estimate_tokens(content)
            if tokens > remaining:
                # Block-level truncation preserves source boundaries instead of mixing histories.
                ratio = remaining / max(tokens, 1)
                content = content[:max(0, int(len(content) * ratio))]
                tokens = estimate_tokens(content)
                reason = "token_budget"
            else:
                reason = ""
            if not content:
                return
            blocks.append(ContextBlock(kind, content, source_ids, priority, tokens, reason, score))
            seen_sources.update(source_ids); remaining -= tokens

        scope = request.metadata.get("scope_id") or ("personal" if policy.mode == "assistant" else "knowledge")
        # QA owns its topic-shaped history in QaService. Injecting it here as well
        # duplicates the same facts and makes the token ledger misleading.
        if policy.mode in {"assistant", "coding"}:
            history = self.app.repository.track_history(mode=policy.mode, scope_id=scope, track_id=request.track_id, limit=32)
            recent = history[-8:]
            earlier = history[:-8]
            if earlier:
                # Deterministic rolling summary keeps confirmed conversational state
                # bounded without pretending the complete factual archive was deleted.
                lines = [f"{item.role}: {self.stringify(item.content)[:180]}" for item in earlier if item.role in {"user", "assistant"}]
                add("rolling_summary", "<rolling_summary>\n" + "\n".join(lines[-12:]) + "\n</rolling_summary>", [item.message_id for item in earlier], 90)
            if recent:
                lines = [f"{item.role}: {self.stringify(item.content)[:700]}" for item in recent if item.role in {"user", "assistant"}]
                add("recent_facts", "<recent_facts>\n" + "\n".join(lines) + "\n</recent_facts>", [item.message_id for item in recent], 100)

        if policy.include_daily and policy.mode == "assistant":
            daily = self.app.runtime.conversation_memory.current_daily_context()
            add("daily_memory", daily, list(getattr(self.app.runtime.conversation_memory, "last_daily_ids", [])), 70)

        if policy.include_memory and policy.mode == "assistant":
            retriever = getattr(self.app.runtime, "personal_memory_retriever", None)
            if retriever is not None:
                memory = retriever.context(request.text, mode="assistant", repository_id="")
                ids = list(getattr(retriever, "last_retrieved_ids", []))
                if not set(ids) & seen_sources:
                    add("long_term_memory", memory, ids, 80, 1.0)

        if policy.include_personal_state:
            add("personal_state", self.app.runtime.personal_state.context(limit=6), priority=60)
        if policy.include_attachments:
            ids = list(request.metadata.get("attachment_ids") or [])
            if ids:
                text, _ = self.app.attachments.context(ids, max_chars=max(1_000, remaining * 4))
                add("attachments", text, ids, 50)
        if policy.include_repository and request.metadata.get("repository_root"):
            add("repository", f"Repository boundary: {request.metadata['repository_root']}", priority=40)
        return BuiltContext(blocks)

    def stringify(self, value: object) -> str:
        if isinstance(value, str): return value
        if isinstance(value, list): return " ".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in value)
        return str(value)
