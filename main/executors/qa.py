from __future__ import annotations
from main.runtime.models import UnifiedRunResult
class QaExecutor:
    def __init__(self, application): self.app=application
    def execute(self, request, context, decision):
        topic=request.metadata.get("topic_id") or self.app.qa.active_topic()
        return UnifiedRunResult(request.run_id,"completed",self.app.qa.ask(request.text,topic),metadata={"executor":"qa","topic_id":topic})
