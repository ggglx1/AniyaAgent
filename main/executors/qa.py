from __future__ import annotations
from main.runtime.models import UnifiedRunResult
from main.llm.usage import bind_request_context
class QaExecutor:
    def __init__(self, application): self.app=application
    def execute(self, request, context, decision):
        context["token"].check()
        topic=request.metadata.get("topic_id") or self.app.qa.active_topic()
        built = context["built_context"]
        images = self.app.attachments.model_image_blocks(list(request.metadata.get("attachment_ids") or []), owner_id=request.user_id)
        run_context = {"run_id": request.run_id, "executor": "qa", "route": decision.run_type, "context_blocks": built.metadata().get("blocks", [])}
        with bind_request_context(run_context):
            output=self.app.qa.ask(request.text,topic, token=context["token"], built_context=built, image_blocks=images, run_context=run_context)
        context["token"].check()
        return UnifiedRunResult(request.run_id,"completed",output,metadata={"executor":"qa","topic_id":topic})
