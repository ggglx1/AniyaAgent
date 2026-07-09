from abc import ABC, abstractmethod

from main.llm.http import ApiAuthError, ApiConfigError, ApiConnectionError, ApiTimeoutError


class ErrorHandler(ABC):
    @abstractmethod
    def handle(self, messages: list, exc: Exception) -> bool:
        pass

    @abstractmethod
    def append_message(self, messages: list, title: str, message: str) -> None:
        pass


class DirectErrorHandler(ErrorHandler):
    def handle(self, messages: list, exc: Exception) -> bool:
        title = self.title_for(exc)
        if title is None:
            return False

        self.append_message(messages, title, str(exc))
        return True

    def append_message(self, messages: list, title: str, message: str) -> None:
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": f"[{title}] {message}",
                    }
                ],
            }
        )

    def title_for(self, exc: Exception) -> str | None:
        if isinstance(exc, ApiAuthError):
            return "API authentication error"
        if isinstance(exc, ApiConfigError):
            return "API configuration error"
        if isinstance(exc, ApiConnectionError):
            return "API connection error"
        if isinstance(exc, ApiTimeoutError):
            return "API timeout error"
        return None
