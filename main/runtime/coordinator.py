from __future__ import annotations

import threading
import time

from main.application.run_events import RunEventStore
from main.actions import PendingActionStore
from main.capabilities import CapabilityCatalog
from main.events import DomainEventOutbox
from main.executors import CodingExecutor, DeliberativeExecutor, DirectConversationExecutor, ProactiveExecutor, QaExecutor, StructuredActionExecutor
from .cancellation import CancellationToken, RunCancelledError, RunTimedOutError
from .context_builder import ContextBuilder
from .context_policy import ContextPolicy
from .models import RunRequest, RunStatus, TERMINAL_RUN_STATUSES, UnifiedRunResult
from .router import RunRouter


class RunCoordinator:
    """The only owner of interactive run state, deadline, cancellation and final facts."""

    def __init__(self, application, max_run_seconds: int = 600):
        self.app = application
        self.max_run_seconds = max_run_seconds
        self.run_events = RunEventStore(application.runtime.WORKDIR)
        self.pending_actions = PendingActionStore(application.runtime.WORKDIR)
        self.context_builder = ContextBuilder(application)
        self.capabilities = CapabilityCatalog(application)
        self.outbox = DomainEventOutbox(application.runtime.WORKDIR)
        self.router = RunRouter(self.pending_actions)
        self.executors = {
            "direct_conversation": DirectConversationExecutor(application),
            "structured_action": StructuredActionExecutor(application),
            "deliberative_agent": DeliberativeExecutor(application),
            "qa": QaExecutor(application),
            "coding": CodingExecutor(application),
            "proactive": ProactiveExecutor(application),
        }
        self._locks: dict[str, tuple[threading.Lock, float]] = {}
        self._guard = threading.RLock()

    def execute(self, request: RunRequest, emit=None) -> UnifiedRunResult:
        emit_external = emit or (lambda *_: None)
        self.ensure_run(request)
        lock = self.lock_for(request.track_id or request.conversation_id)
        if not lock.acquire(blocking=False):
            return self.finish(request, UnifiedRunResult(request.run_id, RunStatus.FAILED.value, error="Another run is active for this conversation.", error_code="run_conflict"))
        deadline = time.monotonic() + self.max_run_seconds
        token = CancellationToken(deadline)

        def emit_event(kind: str, payload: dict | None = None) -> None:
            if self.run_events.is_cancelled(request.run_id):
                token.cancel()
            self.run_events.publish(request.run_id, kind, payload or {})
            emit_external(kind, payload or {})

        try:
            token.check()
            emit_event("running", {"track_id": request.track_id})
            decision = self.router.route(request)
            emit_event("run.routed", {"decision": decision.to_dict()})
            if decision.run_type == "waiting_input":
                return self.finish(request, UnifiedRunResult(request.run_id, RunStatus.WAITING_INPUT.value, "Please provide the missing information to continue.", metadata={"missing_fields": decision.missing_fields, "pending_action": self.pending_actions.get(request.track_id, request.user_id)}), decision=decision)
            executor = self.executors.get(decision.run_type)
            if executor is None:
                return self.finish(request, UnifiedRunResult(request.run_id, RunStatus.FAILED.value, error=f"Unsupported run type: {decision.run_type}", error_code="unsupported_executor"))
            policy = ContextPolicy.for_run_type(decision.run_type)
            context = {
                "emit": emit_event,
                "token": token,
                "deadline_at": deadline,
                "pending_actions": self.pending_actions,
                "capabilities": self.capabilities.select(decision.required_capabilities, decision.run_type),
                "built_context": self.context_builder.build(request, policy),
            }
            emit_event("executor.started", {"executor": decision.run_type})
            result = executor.execute(request, context, decision)
            token.check()
            return self.finish(request, result, decision=decision, context=context)
        except RunCancelledError as exc:
            return self.finish(request, UnifiedRunResult(request.run_id, RunStatus.CANCELLED.value, error=str(exc), error_code="cancelled"))
        except RunTimedOutError as exc:
            return self.finish(request, UnifiedRunResult(request.run_id, RunStatus.TIMED_OUT.value, error=str(exc), error_code="deadline_exceeded"))
        except Exception as exc:
            return self.finish(request, UnifiedRunResult(request.run_id, RunStatus.FAILED.value, error=f"{type(exc).__name__}: {exc}", error_code=type(exc).__name__))
        finally:
            lock.release()
            self.cleanup_locks()

    def ensure_run(self, request: RunRequest) -> None:
        if self.run_events.state(request.run_id) is None:
            self.run_events.create(request.run_id, request.conversation_id, request.track_id, request.user_id)

    def finish(self, request: RunRequest, result: UnifiedRunResult, *, decision=None, context: dict | None = None) -> UnifiedRunResult:
        status = str(result.status)
        if status == "pending_confirmation":  # Compatibility with older executors.
            status = RunStatus.WAITING_CONFIRMATION.value
        if status == "pending_input":
            status = RunStatus.WAITING_INPUT.value
        result.status = status
        metadata = {**result.metadata, "route": decision.to_dict() if decision else result.metadata.get("route", {})}
        if context:
            metadata["context"] = context["built_context"].metadata()
            metadata["capabilities"] = [item.id for item in context["capabilities"]]
        result.metadata = metadata
        if status in {RunStatus.WAITING_INPUT.value, RunStatus.WAITING_CONFIRMATION.value}:
            self.persist_facts(request, result, schedule_memory=False)
            self.run_events.publish(request.run_id, status, {"content": result.output, "metadata": metadata, "error_code": result.error_code})
            return result
        if status not in TERMINAL_RUN_STATUSES:
            result.status = RunStatus.FAILED.value; result.error_code = result.error_code or "invalid_executor_status"; result.error = result.error or f"Invalid executor status: {status}"
        if result.status == RunStatus.COMPLETED.value:
            self.persist_facts(request, result)
            self.run_events.finish(request.run_id, result.status, content=result.output, metadata=result.metadata)
            superseded = str(request.metadata.get("supersedes_waiting_run_id") or "")
            if superseded:
                self.run_events.supersede_waiting(superseded, request.run_id)
            if result.metadata.get("executor") != "proactive":
                event_type = "coding.completed" if request.mode == "coding" else (
                    "qa.conversation.completed" if request.mode == "qa" else
                    "assistant.action.completed" if result.metadata.get("executor") == "structured_action" else
                    "deliberative.completed" if result.metadata.get("executor") == "deliberative" else
                    "assistant.conversation.completed"
                )
                self.outbox.publish(event_type, request.run_id, {"track_id": request.track_id, "mode": request.mode, "repository_id": request.metadata.get("repository_id", ""), "work_session_id": request.metadata.get("work_session_id", ""), "factual_message_ids": result.metadata.get("factual_message_ids", [])})
            for action in result.metadata.get("executed_actions", []):
                self.outbox.publish("action.executed", request.run_id, {"track_id": request.track_id, "action": action})
        else:
            self.run_events.finish(request.run_id, result.status, error_code=result.error_code or result.status, error_message=result.error, metadata=result.metadata)
            self.outbox.publish("run.failed", request.run_id, {"status": result.status, "error_code": result.error_code, "mode": request.mode})
        return result

    def persist_facts(self, request: RunRequest, result: UnifiedRunResult, *, schedule_memory: bool = True) -> None:
        if result.metadata.get("facts_persisted") or result.metadata.get("executor") == "proactive":
            return
        scope = request.metadata.get("scope_id", "personal" if request.mode == "assistant" else "")
        retention = "qa_30_days" if request.mode == "qa" else "long_term"
        expires_at = ""
        if request.mode == "qa":
            from datetime import datetime, timedelta, timezone
            expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace("+00:00", "Z")
        user = self.app.repository.append_track_message("user", request.text, mode=request.mode, scope_id=scope, track_id=request.track_id, repository_id=request.metadata.get("repository_id", ""), work_session_id=request.metadata.get("work_session_id", ""), topic_id=request.metadata.get("topic_id", ""), retention_class=retention, expires_at=expires_at, metadata={"run_id": request.run_id, "executor": result.metadata.get("executor", "")})
        assistant = self.app.repository.append_track_message("assistant", result.output, mode=request.mode, scope_id=scope, track_id=request.track_id, repository_id=request.metadata.get("repository_id", ""), work_session_id=request.metadata.get("work_session_id", ""), topic_id=request.metadata.get("topic_id", ""), retention_class=retention, expires_at=expires_at, reply_to_message_id=user.message_id, metadata={"run_id": request.run_id, "executor": result.metadata.get("executor", "")})
        result.metadata["factual_message_ids"] = [user.message_id, assistant.message_id]
        # Memory Pipeline is invoked only by the durable Domain Event consumer.

    def lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lock, _ = self._locks.get(key, (threading.Lock(), 0.0))
            self._locks[key] = (lock, time.monotonic())
            return lock

    def cleanup_locks(self) -> None:
        cutoff = time.monotonic() - 3600
        with self._guard:
            self._locks = {key: value for key, value in self._locks.items() if value[0].locked() or value[1] >= cutoff}
