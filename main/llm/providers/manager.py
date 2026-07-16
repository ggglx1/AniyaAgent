import threading
import os

from main.llm.http.config import (
    ensure_configured,
    get_settings,
    persist_selected_provider,
    provider_configs,
    normalize_provider_name,
    selected_provider_name,
)

from .anthropic import AnthropicProvider
from .openai_compatible import OpenAICompatibleProvider


class ProviderManager:
    def __init__(self):
        self.providers = {
            "anthropic": AnthropicProvider(),
            "openai": OpenAICompatibleProvider(),
        }
        self.lock = threading.RLock()
        self.active_name = selected_provider_name()
        if self.active_name not in self.providers:
            self.active_name = "anthropic"

    def create_message(self, *, model=None, system, messages, tools, max_tokens=8000):
        with self.lock:
            active_name = self.active_name
        settings = ensure_configured(active_name)
        config = provider_configs()[active_name]
        resolved_model = self.resolve_model(model, config.model)
        return self.providers[active_name].create_message(
            config,
            model=resolved_model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )

    def resolve_model(self, requested_model: str | None, active_default: str) -> str:
        if not requested_model:
            return active_default
        known_defaults = {config.model for config in provider_configs().values()}
        legacy_model = os.getenv("MODEL_ID")
        if legacy_model:
            known_defaults.add(legacy_model)
        if requested_model in known_defaults:
            return active_default
        return requested_model

    def list_providers(self) -> dict:
        with self.lock:
            active_name = self.active_name
        configs = provider_configs()
        return {
            "active": active_name,
            "providers": [
                configs[name].public_dict(active=name == active_name)
                for name in self.providers
            ],
        }

    def select_provider(self, name: str) -> dict:
        normalized = normalize_provider_name(name)
        if normalized not in self.providers:
            raise ValueError(f"Unknown LLM provider: {normalized}")
        settings = get_settings(normalized)
        if not settings.configured:
            variable = "ANTHROPIC_API_KEY" if normalized == "anthropic" else "OPENAI_API_KEY"
            raise ValueError(f"Provider {normalized} is not configured; missing {variable}")
        with self.lock:
            self.active_name = normalized
            persist_selected_provider(normalized)
        return self.list_providers()

    def active_settings(self):
        with self.lock:
            return get_settings(self.active_name)
