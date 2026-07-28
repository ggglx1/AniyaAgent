from __future__ import annotations

from main.runtime.models import UnifiedRunResult
from main.llm.usage import bind_request_context, current_request_context


class DirectConversationExecutor:
    """One LLM request, no tool schemas, skills, MCP or full personal-state context."""
    def __init__(self, application): self.app = application
    def execute(self, request, context, decision):
        loop = self.app.runtime
        context["token"].check()
        built = context["built_context"]
        memory = built.text
        system = "You are Aniya, a warm, truthful personal companion. Reply directly. Do not claim to perform actions or use tools."
        text = f"{memory}\n\n{request.text}" if memory else request.text
        image_blocks = self.app.attachments.model_image_blocks(list(request.metadata.get("attachment_ids") or []), owner_id=request.user_id)
        content = [{"type": "text", "text": text}, *image_blocks] if image_blocks else text
        run_context = {"run_id": request.run_id, "executor": "direct_conversation", "route": decision.run_type, "context_blocks": built.metadata().get("blocks", [])}
        with bind_request_context({**current_request_context(), **run_context}):
            response = loop.llm_gateway.messages.create(task_type="main", model=loop.MODEL, max_tokens=1024, system=system, messages=[{"role":"user","content":content}], tools=[], cancellation_token=context["token"], run_context=run_context)
        context["token"].check()
        output = loop.extract_text(response.content).strip()
        if not output:
            return UnifiedRunResult(request.run_id, "failed", error="Model returned an empty response.", error_code="empty_model_output", metadata={"executor":"direct_conversation"})
        return UnifiedRunResult(request.run_id, "completed", output, metadata={"executor":"direct_conversation", "memory_sources":built.source_ids, "attachment_sources":[item.get("attachment_id", "") for item in image_blocks]})
