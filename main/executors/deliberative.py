from __future__ import annotations

from main.runtime.deliberative_runtime import DeliberativeRuntimeAdapter


class DeliberativeExecutor:
    """Coordinator-owned bounded ReAct executor."""
    def __init__(self, application): self.runtime = DeliberativeRuntimeAdapter(application)
    def execute(self, request, context, decision):
        return self.runtime.execute(request, context)
