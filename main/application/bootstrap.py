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
        self.scheduler = SchedulerService(main_loop, self.repository)

    def web_runtime(self): return self._runtime.get_channel_runtime()
    def start_scheduler(self): return self.lifecycle.start_once(self.scheduler.start)
    def stop(self): self.lifecycle.stop(self.scheduler.stop)

    def handle_mode(self, mode: str, text: str, **kwargs):
        """Backend mode router used by Web/CLI adapters without exposing cross-track state."""
        if mode == "assistant":
            return self.assistant.handle(text, **kwargs)
        if mode == "qa":
            topic_id = kwargs.get("topic_id") or self.qa.active_topic()
            return {"topic_id": topic_id, "text": self.qa.ask(text, topic_id)}
        if mode == "coding":
            repository_root = kwargs.get("repository_root")
            if not repository_root: raise ValueError("repository_root is required for coding mode")
            return self.coding.handle(text, repository_root, kwargs.get("work_session_id", ""))
        raise ValueError(f"Unsupported conversation mode: {mode}")
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
