from main.llm.models import MessageResponse

from .base import LlmProvider, ProviderConfig
from .transport import post_json


class AnthropicProvider(LlmProvider):
    name = "anthropic"

    def create_message(self, config: ProviderConfig, **kwargs) -> MessageResponse:
        url, payload, headers = self.build_request(config, **kwargs)
        return self.parse_response(post_json(url, payload, headers))

    def build_request(self, config: ProviderConfig, **kwargs) -> tuple[str, dict, dict]:
        payload = {
            "model": kwargs["model"],
            "system": kwargs["system"],
            "messages": kwargs["messages"],
            "tools": kwargs["tools"],
            "max_tokens": kwargs["max_tokens"],
        }
        headers = {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": config.api_key,
        }
        return self.endpoint(config.base_url, "/v1/messages"), payload, headers

    def parse_response(self, raw: dict) -> MessageResponse:
        return MessageResponse(
            content=list(raw.get("content") or []),
            stop_reason=str(raw.get("stop_reason") or "end_turn"),
            raw=raw,
        )
