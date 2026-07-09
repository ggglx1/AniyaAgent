import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from AuditLog import AuditLog
from ConversationStore import ConversationStore
from RuntimeContext import bind_runtime, clear_runtime


@dataclass
class RunResult:
    run_id: str
    session_id: str
    status: str
    output: str = ""
    error: str = ""
    started_at: float = 0
    finished_at: float = 0


class AgentRuntime:
    def __init__(
        self,
        workdir: Path,
        agent_loop: Callable[[list], None],
        extract_text: Callable[[object], str],
        max_run_seconds: int = 600,
    ):
        self.workdir = workdir.resolve()
        self.agent_loop = agent_loop
        self.extract_text = extract_text
        self.max_run_seconds = max_run_seconds
        self.conversations = ConversationStore(self.workdir)
        self.audit = AuditLog(self.workdir)
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
            messages = self.conversations.load(session_id)
            messages.append({"role": "user", "content": user_text})
            self.conversations.save(session_id, messages)
            checkpoint_path = self.conversations.checkpoint(session_id, run_id, messages)
            self.audit.write(run_id, "checkpoint.before", {"path": str(checkpoint_path)})

            deadline = started_at + self.max_run_seconds
            bind_runtime(run_id, session_id, self.audit, self.conversations, event_callback)
            self.agent_loop(messages)
            if time.time() > deadline:
                raise TimeoutError(f"Run exceeded {self.max_run_seconds} seconds.")

            self.conversations.save(session_id, messages)
            checkpoint_path = self.conversations.checkpoint(session_id, run_id, messages)
            output = self.latest_assistant_text(messages)
            result = RunResult(
                run_id=run_id,
                session_id=session_id,
                status="completed",
                output=output,
                started_at=started_at,
                finished_at=time.time(),
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
