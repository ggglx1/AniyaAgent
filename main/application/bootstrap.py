from __future__ import annotations

from main.agent import main_loop
from main.conversation import ConversationMemoryRepository
from main.attachments import AttachmentService
from main.mcp import McpGateway
from .coding_assistant import CodingAssistantService
from .lifecycle import Lifecycle
from .personal_assistant import PersonalAssistantService
from .qa_service import QaService
from .scheduler_service import SchedulerService
from main.runtime import RunCoordinator
from main.runtime.models import RunRequest
import uuid


class AniyaApplication:
    """Composition root. Expensive coding dependencies remain behind the developer adapter."""

    def __init__(self):
        self._runtime = main_loop
        # Compatibility for channels that still need the controlled runtime adapters.
        self.runtime = main_loop
        self.lifecycle = Lifecycle()
        self.repository = ConversationMemoryRepository(main_loop.WORKDIR)
        self.attachments = AttachmentService(main_loop.WORKDIR)
        self.mcp = McpGateway(main_loop.WORKDIR)
        self.assistant = PersonalAssistantService(main_loop.get_channel_runtime(), main_loop.conversation_memory)
        self.coding = CodingAssistantService(main_loop.WORKDIR, self.repository, main_loop.get_channel_runtime)
        self.qa = QaService(main_loop.llm_gateway, main_loop.MODEL, self.repository)
        self.run_coordinator = RunCoordinator(self)
        self.scheduler = SchedulerService(main_loop, self.repository, self)

    def web_runtime(self): return self._runtime.get_channel_runtime()
    def start_scheduler(self): return self.lifecycle.start_once(self.scheduler.start)
    def stop(self): self.lifecycle.stop(self.scheduler.stop)

    def handle_mode(self, mode: str, text: str, **kwargs):
        """Compatibility adapter. Product execution always enters RunCoordinator."""
        mode = str(mode).lower()
        if mode not in {"assistant", "qa", "coding"}: raise ValueError(f"Unsupported conversation mode: {mode}")
        track = kwargs.get("track") or {"assistant": {"track_id": "assistant:personal", "scope_id": "personal", "conversation_id": "personal"}, "qa": {"track_id": f"qa:{kwargs.get('topic_id') or self.qa.active_topic()}", "scope_id": "knowledge", "conversation_id": kwargs.get('topic_id') or self.qa.active_topic()}, "coding": {"track_id": kwargs.get("track_id", ""), "scope_id": kwargs.get("repository_id", ""), "conversation_id": kwargs.get("work_session_id", "")}}[mode]
        request = RunRequest(f"compat_{uuid.uuid4().hex}", "local", str(kwargs.get("channel_id") or "compat"), str(track["conversation_id"]), mode, str(track["track_id"]), text, {**kwargs, "scope_id": track["scope_id"]})
        return self.run_coordinator.execute(request).to_dict()
    @property
    def memory_admin_dependencies(self):
        return (
            self._runtime.conversation_memory,
            self._runtime.personal_memory_manager,
            self._runtime.personal_state,
            self._runtime.routine_manager,
        )


def create_application() -> AniyaApplication:
    return AniyaApplication()
