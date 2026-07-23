from __future__ import annotations
from main.runtime.models import UnifiedRunResult
class CodingExecutor:
    def __init__(self, application): self.app=application
    def execute(self, request, context, decision):
        root=request.metadata.get("repository_root")
        if not root: return UnifiedRunResult(request.run_id,"failed",error="repository_root is required for Coding")
        result=self.app.coding.handle(request.text,root,request.metadata.get("work_session_id", ""))
        return UnifiedRunResult(request.run_id,"completed",result["text"],metadata={"executor":"coding",**result})
