from __future__ import annotations

"""Explicit production composition facade for the private personal-assistant product."""


class PersonalAssistantApplication:
    def __init__(self):
        # Kept lazy so importing entry points never starts workers.
        from main.agent import main_loop
        self.runtime = main_loop

    def web_runtime(self):
        return self.runtime.get_channel_runtime()

    def start_scheduler(self) -> None:
        self.runtime.start_background_services()

    @property
    def memory_admin_dependencies(self):
        return self.runtime.conversation_memory, self.runtime.personal_memory_manager


def create_application() -> PersonalAssistantApplication:
    return PersonalAssistantApplication()
