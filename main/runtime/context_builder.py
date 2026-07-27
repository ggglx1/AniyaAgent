from __future__ import annotations

from dataclasses import dataclass, field

from .context_policy import ContextPolicy


@dataclass
class ContextBlock:
    kind: str
    content: object
    source_ids: list[str] = field(default_factory=list)
    priority: int = 0
    estimated_tokens: int = 0
    trimmed_reason: str = ""


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
        return {"blocks": [{"kind": b.kind, "source_ids": b.source_ids, "estimated_tokens": b.estimated_tokens, "trimmed_reason": b.trimmed_reason} for b in self.blocks]}


class ContextBuilder:
    """Builds bounded, executor-specific context without exposing global runtime state."""

    def __init__(self, application):
        self.app = application

    def build(self, request, policy: ContextPolicy) -> BuiltContext:
        blocks: list[ContextBlock] = []
        remaining = policy.max_chars

        def add(kind: str, content: str, source_ids: list[str] | None = None, priority: int = 0):
            nonlocal remaining
            if not content or remaining <= 0:
                return
            value = content[:remaining]
            blocks.append(ContextBlock(kind, value, source_ids or [], priority, max(1, len(value) // 4), "budget" if len(value) < len(content) else ""))
            remaining -= len(value)

        if policy.include_memory:
            retriever = getattr(self.app.runtime, "personal_memory_retriever", None)
            if retriever is not None:
                add("long_term_memory", retriever.context(request.text, mode=policy.mode, repository_id=request.metadata.get("repository_id", "")), list(getattr(retriever, "last_retrieved_ids", [])), 90)
            memory_context = getattr(self.app.runtime, "memory_context", None)
            if memory_context is not None:
                add("recent_memory", memory_context.assemble(request.text, mode=policy.mode), priority=80)
        if policy.include_personal_state:
            add("personal_state", self.app.runtime.personal_state.context(), priority=70)
        if policy.include_attachments:
            attachment_ids = list(request.metadata.get("attachment_ids") or [])
            if attachment_ids:
                text, _ = self.app.attachments.context(attachment_ids, max_chars=remaining)
                add("attachments", text, attachment_ids, 60)
        if policy.include_repository:
            root = request.metadata.get("repository_root")
            if root:
                add("repository", f"Repository boundary: {root}", priority=50)
        return BuiltContext(blocks)
