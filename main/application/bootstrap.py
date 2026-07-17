from __future__ import annotations

from main.agent import main_loop
from main.conversation import ConversationMemoryRepository
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
        self.assistant = PersonalAssistantService(main_loop.get_channel_runtime(), main_loop.conversation_memory)
        self.coding = CodingAssistantService(main_loop.WORKDIR, self.repository, main_loop.get_channel_runtime)
        self.qa = QaService(main_loop.llm_gateway, main_loop.MODEL, self.repository)
        self.scheduler = SchedulerService(main_loop, self.repository)

    def web_runtime(self): return self._runtime.get_channel_runtime()
    def start_scheduler(self): return self.lifecycle.start_once(self.scheduler.start)
    def stop(self): self.lifecycle.stop(self.scheduler.stop)
    @property
    def memory_admin_dependencies(self): return self._runtime.conversation_memory, self._runtime.personal_memory_manager


def create_application() -> AniyaApplication:
    return AniyaApplication()
