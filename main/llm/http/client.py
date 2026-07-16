from main.llm.providers.manager import ProviderManager


class MessagesClient:
    def __init__(self, provider_manager: ProviderManager):
        self.provider_manager = provider_manager

    def create(self, *, model=None, system, messages, tools, max_tokens=8000):
        return self.provider_manager.create_message(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )


class LLMClient:
    def __init__(self, provider_manager: ProviderManager | None = None):
        self.provider_manager = provider_manager or ProviderManager()
        self.messages = MessagesClient(self.provider_manager)

    def list_providers(self) -> dict:
        return self.provider_manager.list_providers()

    def select_provider(self, name: str) -> dict:
        return self.provider_manager.select_provider(name)

    def active_settings(self):
        return self.provider_manager.active_settings()


client = LLMClient()
