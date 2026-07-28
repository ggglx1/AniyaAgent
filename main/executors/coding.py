from __future__ import annotations
from main.runtime.models import UnifiedRunResult
class CodingExecutor:
    def __init__(self, application): self.app=application
    def execute(self, request, context, decision):
        root=request.metadata.get("repository_root")
        if not root: return UnifiedRunResult(request.run_id,"failed",error="repository_root is required for Coding")
        context["token"].check()
        built = context["built_context"]
        run_context = {"run_id": request.run_id, "executor": "coding", "route": decision.run_type, "context_blocks": built.metadata().get("blocks", [])}
        result=self.app.coding.handle(request.text,root,request.metadata.get("work_session_id", ""), token=context["token"], run_context=run_context)
        status_map = {"completed": "completed", "budget_exhausted": "failed", "incomplete": "failed"}
        status = status_map.get(result.get("status"), "failed")
        error_code = "coding_budget_reached" if result.get("status") == "budget_exhausted" else ("coding_incomplete" if status != "completed" else "")
        return UnifiedRunResult(request.run_id,status,result["text"],error_code=error_code,metadata={"executor":"coding",**result})
