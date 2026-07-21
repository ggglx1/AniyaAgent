import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from main.storage.audit_log import AuditLog
from main.storage.conversation_store import ConversationStore
from main.agent.runtime_context import bind_runtime, clear_runtime
from main.agent.conversation_integrity import ConversationIntegrityValidator
from main.agent.deadline import RunDeadline
from main.conversation import ConversationMemoryRepository, ConversationMemoryService


@dataclass
class RunResult:
    run_id: str
    session_id: str
    status: str
    output: str = ""
    error: str = ""
    started_at: float = 0
    finished_at: float = 0
    memory_sources: dict = None


class AgentRuntime:
    def __init__(
        self,
        workdir: Path,
        agent_loop: Callable[[list], None],
        extract_text: Callable[[object], str],
        max_run_seconds: int = 600,
        conversation_memory: ConversationMemoryService | None = None,
        profile_store=None,
        memory_pipeline=None,
        memory_maintenance=None,
        memory_source_provider=None,
    ):
        self.workdir = workdir.resolve()
        self.agent_loop = agent_loop
        self.extract_text = extract_text
        self.max_run_seconds = max_run_seconds
        self.conversations = ConversationStore(self.workdir)
        self.conversation_memory = conversation_memory or ConversationMemoryService(ConversationMemoryRepository(self.workdir))
        self.profile_store = profile_store
        self.memory_pipeline = memory_pipeline
        self.memory_maintenance = memory_maintenance
        self.memory_source_provider = memory_source_provider
        self.audit = AuditLog(self.workdir)
        self.integrity = ConversationIntegrityValidator()
        self.session_locks: dict[str, threading.Lock] = {}
        self.session_locks_guard = threading.Lock()

    def run(self, session_id: str, user_text: str) -> RunResult:
        return self.run_with_context(
            session_id=session_id,
            user_text=user_text,
            channel_context={},
        )

    def handle_message(self, channel_message, event_callback: Callable[[str, dict], None] | None = None) -> RunResult:
        return self.run_with_context(
            session_id=channel_message.session_id,
            user_text=channel_message.text,
            channel_context={
                "channel_id": channel_message.channel_id,
                "user_id": channel_message.user_id,
                "conversation_id": channel_message.conversation_id,
                "kind": channel_message.kind.value,
                "trust_level": channel_message.trust_level.value,
                "files": channel_message.files,
                "images": channel_message.images,
                "metadata": channel_message.metadata,
            },
            event_callback=event_callback,
        )

    def run_with_context(
        self,
        session_id: str,
        user_text: str,
        channel_context: dict,
        event_callback: Callable[[str, dict], None] | None = None,
    ) -> RunResult:
        run_id = self.new_run_id()
        lock = self.lock_for(session_id)
        started_at = time.time()

        if not lock.acquire(blocking=False):
            result = RunResult(
                run_id=run_id,
                session_id=session_id,
                status="rejected",
                error="Another run is already active for this session.",
                started_at=started_at,
                finished_at=time.time(),
                memory_sources=self.memory_sources(),
            )
            self.audit.write(run_id, "run.rejected", asdict(result))
            return result

        try:
            self.audit.write(
                run_id,
                "run.started",
                {
                    "session_id": session_id,
                    "input_preview": user_text[:500],
                    "max_run_seconds": self.max_run_seconds,
                    "channel": channel_context,
                },
            )
            messages = self.load_clean_context(session_id, run_id)
            initial_message_count = len(messages)
            messages.append({"role": "user", "content": user_text})
            factual_ids: list[str] = []
            if self.is_web_context(channel_context):
                timezone_name = self.timezone_for_web()
                # Web only records facts and dirty work. Scheduler is the sole maintenance owner.
                self.conversation_memory.repository.request_maintenance("memory_maintenance", {"timezone": timezone_name})
                factual_ids.append(
                    self.conversation_memory.repository.append_message(
                        "user", user_text, timezone_name=timezone_name, metadata={"channel": "web"}
                    ).message_id
                )
            self.conversations.save_working(session_id, messages)
            checkpoint_path = self.conversations.checkpoint(session_id, run_id, messages)
            self.audit.write(run_id, "checkpoint.before", {"path": str(checkpoint_path)})

            deadline = RunDeadline.after(self.max_run_seconds)
            bind_runtime(run_id, session_id, self.audit, self.conversations, event_callback, deadline)
            self.agent_loop(messages)
            deadline.require_remaining()

            self.ensure_clean_or_recover(session_id, run_id, messages)
            self.conversations.save(session_id, messages)
            if self.is_web_context(channel_context):
                factual_ids.extend(
                    self.conversation_memory.append_runtime_messages(
                        messages, initial_message_count + 1, self.timezone_for_web()
                    )
                )
                if self.memory_pipeline is not None:
                    self.memory_pipeline.process(factual_ids, user_id="local")
            checkpoint_path = self.conversations.checkpoint(session_id, run_id, messages)
            output = self.latest_assistant_text(messages)
            result = RunResult(
                run_id=run_id,
                session_id=session_id,
                status="completed",
                output=output,
                started_at=started_at,
                finished_at=time.time(),
                memory_sources=self.memory_sources(),
            )
            self.audit.write(run_id, "checkpoint.after", {"path": str(checkpoint_path)})
            self.audit.write(run_id, "run.completed", asdict(result))
            return result
        except Exception as exc:
            try:
                self.conversations.checkpoint(
                    session_id,
                    f"{run_id}_failed",
                    locals().get("messages", []),
                )
            except Exception:
                pass

            result = RunResult(
                run_id=run_id,
                session_id=session_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                finished_at=time.time(),
                memory_sources=self.memory_sources(),
            )
            self.audit.write(
                run_id,
                "run.failed",
                {
                    **asdict(result),
                    "traceback": traceback.format_exc(),
                },
            )
            return result
        finally:
            clear_runtime()
            lock.release()

    def load_clean_context(self, session_id: str, run_id: str) -> list:
        messages = self.conversations.load(session_id)
        report = self.integrity.validate(messages)
        if report.valid: return messages
        self.conversations.quarantine(session_id, run_id, messages, "loaded context integrity failure", report.errors)
        repaired, repaired_report = self.integrity.repair(messages)
        if repaired_report.valid:
            self.conversations.save(session_id, repaired)
            self.audit.write(run_id, "context.recovered", {"errors": report.errors, "quarantined": len(repaired_report.quarantined)})
            return repaired
        fallback = self.conversations.load_last_known_good(session_id)
        if fallback and self.integrity.validate(fallback).valid:
            self.audit.write(run_id, "context.rolled_back", {"errors": report.errors})
            return fallback
        return []

    def ensure_clean_or_recover(self, session_id: str, run_id: str, messages: list) -> None:
        report = self.integrity.validate(messages)
        if report.valid: return
        self.conversations.quarantine(session_id, run_id, messages, "working context integrity failure", report.errors)
        repaired, repaired_report = self.integrity.repair(messages)
        if not repaired_report.valid:
            raise RuntimeError("Conversation recovery could not produce a valid context")
        messages[:] = repaired
        self.audit.write(run_id, "context.recovered", {"errors": report.errors, "quarantined": len(repaired_report.quarantined)})

    def latest_assistant_text(self, messages: list) -> str:
        for message in reversed(messages):
            if message.get("role") == "assistant":
                return self.extract_text(message.get("content", ""))
        return ""

    def lock_for(self, session_id: str) -> threading.Lock:
        with self.session_locks_guard:
            if session_id not in self.session_locks:
                self.session_locks[session_id] = threading.Lock()
            return self.session_locks[session_id]

    def new_run_id(self) -> str:
        return f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    def is_web_context(self, context: dict) -> bool:
        return context.get("kind") == "web" or context.get("channel_id") == "web"

    def timezone_for_web(self) -> str:
        if self.profile_store is None:
            return "Asia/Shanghai"
        return str(self.profile_store.get().get("timezone") or "Asia/Shanghai")

    def memory_sources(self) -> dict:
        return dict(self.memory_source_provider() or {}) if self.memory_source_provider else {}
