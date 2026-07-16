import json

from main.llm.models import MessageResponse

from .base import LlmProvider, ProviderConfig
from .transport import post_json


class OpenAICompatibleProvider(LlmProvider):
    name = "openai"

    def create_message(self, config: ProviderConfig, **kwargs) -> MessageResponse:
        url, payload, headers = self.build_request(config, **kwargs)
        return self.parse_response(post_json(url, payload, headers))

    def build_request(self, config: ProviderConfig, **kwargs) -> tuple[str, dict, dict]:
        payload = {
            "model": kwargs["model"],
            "messages": self.convert_messages(kwargs["system"], kwargs["messages"]),
            "max_tokens": kwargs["max_tokens"],
        }
        tools = kwargs.get("tools") or []
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                    },
                }
                for tool in tools
            ]
            payload["tool_choice"] = "auto"
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {config.api_key}",
        }
        return self.endpoint(config.base_url, "/v1/chat/completions"), payload, headers

    def convert_messages(self, system: str, messages: list) -> list[dict]:
        converted = [{"role": "system", "content": system}]
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if isinstance(content, str):
                converted.append({"role": role, "content": content})
                continue
            blocks = list(content or [])
            if role == "assistant":
                text_parts = []
                tool_calls = []
                for block in blocks:
                    block_type = self.value(block, "type")
                    if block_type == "text":
                        text_parts.append(str(self.value(block, "text") or ""))
                    elif block_type == "tool_use":
                        tool_calls.append({
                            "id": str(self.value(block, "id")),
                            "type": "function",
                            "function": {
                                "name": str(self.value(block, "name")),
                                "arguments": json.dumps(self.value(block, "input") or {}, ensure_ascii=False),
                            },
                        })
                item = {"role": "assistant", "content": "\n".join(text_parts) or None}
                if tool_calls:
                    item["tool_calls"] = tool_calls
                converted.append(item)
                continue
            text_parts = []
            for block in blocks:
                block_type = self.value(block, "type")
                if block_type == "tool_result":
                    converted.append({
                        "role": "tool",
                        "tool_call_id": str(self.value(block, "tool_use_id")),
                        "content": self.stringify(self.value(block, "content")),
                    })
                elif block_type == "text":
                    text_parts.append(str(self.value(block, "text") or ""))
            if text_parts:
                converted.append({"role": role or "user", "content": "\n".join(text_parts)})
        return converted

    def parse_response(self, raw: dict) -> MessageResponse:
        choices = raw.get("choices") or []
        if not choices:
            return MessageResponse([], "end_turn", raw)
        choice = choices[0]
        message = choice.get("message") or {}
        content = []
        text = message.get("content")
        if text:
            content.append({"type": "text", "text": str(text)})
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = function.get("arguments") or "{}"
            try:
                tool_input = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
            except (json.JSONDecodeError, TypeError, ValueError):
                tool_input = {"_raw_arguments": str(arguments)}
            content.append({
                "type": "tool_use",
                "id": str(call.get("id") or ""),
                "name": str(function.get("name") or ""),
                "input": tool_input,
            })
        finish_reason = str(choice.get("finish_reason") or "stop")
        stop_reason = {
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }.get(finish_reason, "end_turn")
        return MessageResponse(content, stop_reason, raw)

    def value(self, block, key: str):
        if isinstance(block, dict):
            return block.get(key)
        return getattr(block, key, None)

    def stringify(self, value) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)
