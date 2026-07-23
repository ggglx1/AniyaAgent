from __future__ import annotations

import threading

from main.executors import CodingExecutor, DeliberativeExecutor, DirectConversationExecutor, QaExecutor, StructuredActionExecutor
from .models import RunRequest, UnifiedRunResult
from .router import RunRouter


class RunCoordinator:
    """Single lifecycle owner that routes every interactive run to one executor."""
    def __init__(self, application):
        self.app=application; self.router=RunRouter(); self.executors={
            "direct_conversation":DirectConversationExecutor(application), "structured_action":StructuredActionExecutor(application),
            "deliberative_agent":DeliberativeExecutor(application), "qa":QaExecutor(application), "coding":CodingExecutor(application),
        }; self._locks={}; self._guard=threading.RLock()
    def execute(self, request: RunRequest, emit=None) -> UnifiedRunResult:
        emit=emit or (lambda *_: None); lock=self.lock_for(request.track_id or request.conversation_id)
        if not lock.acquire(blocking=False): return UnifiedRunResult(request.run_id,"failed",error="Another run is active for this conversation.")
        try:
            decision=self.router.route(request); emit("run.routed", {"decision":decision.to_dict()}); emit("executor.started", {"executor":decision.run_type})
            executor=self.executors.get(decision.run_type)
            if executor is None: return UnifiedRunResult(request.run_id,"failed",error=f"Unsupported run type: {decision.run_type}")
            result=executor.execute(request,{"emit":emit},decision)
            emit("run.completed" if result.status=="completed" else "action.pending_confirmation", {"status":result.status,"metadata":result.metadata})
            return result
        except Exception as exc:
            emit("run.failed", {"error_type":type(exc).__name__,"message":str(exc)})
            return UnifiedRunResult(request.run_id,"failed",error=f"{type(exc).__name__}: {exc}")
        finally: lock.release()
    def lock_for(self,key):
        with self._guard:
            return self._locks.setdefault(key,threading.Lock())
