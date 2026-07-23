from __future__ import annotations

from main.runtime.models import UnifiedRunResult


class DirectConversationExecutor:
    """One LLM request, no tool schemas, skills, MCP or full personal-state context."""
    def __init__(self, application): self.app = application
    def execute(self, request, context, decision):
        loop = self.app.runtime
        memory = loop.memory_context.assemble(request.text, mode="assistant")
        system = "You are Aniya, a warm, truthful personal companion. Reply directly. Do not claim to perform actions or use tools."
        response = loop.llm_gateway.messages.create(task_type="main", model=loop.MODEL, max_tokens=1024, system=system, messages=[{"role":"user","content":f"{memory}\n\n{request.text}" if memory else request.text}], tools=[])
        output = loop.extract_text(response.content).strip()
        repository = self.app.repository
        user = repository.append_track_message("user", request.text, mode="assistant", scope_id="personal", track_id="assistant:personal", metadata={"run_id":request.run_id, "executor":"direct"})
        assistant = repository.append_track_message("assistant", output, mode="assistant", scope_id="personal", track_id="assistant:personal", reply_to_message_id=user.message_id, metadata={"run_id":request.run_id, "executor":"direct"})
        repository.request_maintenance("memory_pipeline", {"message_ids":[user.message_id, assistant.message_id], "mode":"assistant"})
        return UnifiedRunResult(request.run_id, "completed", output, metadata={"executor":"direct_conversation", "factual_message_ids":[user.message_id, assistant.message_id]})
