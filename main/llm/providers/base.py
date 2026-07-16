from abc import ABC, abstractmethod
from dataclasses import dataclass

from main.llm.models import MessageResponse


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key: str
    base_url: str
    model: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def public_dict(self, active: bool = False) -> dict:
        return {
            "name": self.name,
            "configured": self.configured,
            "active": active,
            "base_url": self.base_url,
            "model": self.model,
        }


class LlmProvider(ABC):
    name: str

    @abstractmethod
    def create_message(
        self,
        config: ProviderConfig,
        *,
        model: str,
        system: str,
        messages: list,
        tools: list,
        max_tokens: int,
    ) -> MessageResponse:
        pass

    @abstractmethod
    def build_request(
        self,
        config: ProviderConfig,
        *,
        model: str,
        system: str,
        messages: list,
        tools: list,
        max_tokens: int,
    ) -> tuple[str, dict, dict]:
        pass

    @abstractmethod
    def parse_response(self, raw: dict) -> MessageResponse:
        pass

    def endpoint(self, base_url: str, path: str) -> str:
        base = base_url.rstrip("/")
        normalized_path = "/" + path.strip("/")
        if base.endswith(normalized_path):
            return base
        path_without_version = normalized_path[3:] if normalized_path.startswith("/v1/") else normalized_path
        if base.endswith(path_without_version):
            return base
        if normalized_path.startswith("/v1/") and base.endswith("/v1"):
            return base + normalized_path[3:]
        return base + normalized_path
