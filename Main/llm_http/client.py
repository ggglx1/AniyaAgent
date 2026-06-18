import json
import socket
import urllib.error
import urllib.request

from .config import ensure_configured


class LLMError(RuntimeError):
    pass


class ApiAuthError(LLMError):
    pass


class ApiConfigError(LLMError):
    pass


class ApiConnectionError(LLMError):
    pass


class ApiTimeoutError(LLMError):
    pass


class ApiHTTPError(LLMError):
    pass


class Block(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class MessageResponse:
    def __init__(self, raw: dict):
        self.raw = raw
        self.content = [Block(block) for block in raw.get("content", [])]
        self.stop_reason = raw.get("stop_reason")


class MessagesClient:
    def create(self, *, model=None, system, messages, tools, max_tokens=8000):
        settings = ensure_configured()
        payload = {
            "model": model or settings.model,
            "system": system,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            f"{settings.base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": settings.api_key,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read().decode("utf-8")
                return MessageResponse(json.loads(body))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            message = f"API error {exc.code}: {body}"
            if exc.code in {401, 403}:
                raise ApiAuthError(message) from exc
            if exc.code == 404:
                raise ApiConfigError(message) from exc
            raise ApiHTTPError(message) from exc
        except TimeoutError as exc:
            raise ApiTimeoutError(f"API timeout: {exc}") from exc
        except socket.timeout as exc:
            raise ApiTimeoutError(f"API timeout: {exc}") from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError) or isinstance(reason, socket.timeout):
                raise ApiTimeoutError(f"API timeout: {reason}") from exc
            raise ApiConnectionError(f"Connection error: {reason}") from exc


class LLMClient:
    def __init__(self):
        self.messages = MessagesClient()


client = LLMClient()
