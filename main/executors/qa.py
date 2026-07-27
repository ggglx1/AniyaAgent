from __future__ import annotations
from main.runtime.models import UnifiedRunResult
class QaExecutor:
    def __init__(self, application): self.app=application
    def execute(self, request, context, decision):
        context["token"].check()
        topic=request.metadata.get("topic_id") or self.app.qa.active_topic()
        output=self.app.qa.ask(request.text,topic)
        context["token"].check()
        return UnifiedRunResult(request.run_id,"completed",output,metadata={"executor":"qa","topic_id":topic})
