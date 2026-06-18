import os
import random
import time
from dataclasses import dataclass


DEFAULT_MAX_TOKENS = 8000
ESCALATED_MAX_TOKENS = 64000
MAX_RECOVERY_RETRIES = 3
MAX_RETRIES = 10
BASE_DELAY_MS = 500
MAX_CONSECUTIVE_529 = 3

CONTINUATION_PROMPT = (
    "Output token limit hit. Resume directly; no apology, no recap. "
    "Pick up exactly where the previous response stopped."
)


@dataclass
class RecoveryState:
    current_model: str
    fallback_model: str | None
    max_tokens: int
    has_escalated_tokens: bool = False
    continuation_count: int = 0
    consecutive_529: int = 0


class ErrorRecovery:
    def __init__(
        self,
        primary_model: str,
        fallback_model: str | None = None,
        sleep_fn=time.sleep,
    ):
        self.primary_model = primary_model
        self.fallback_model = fallback_model or os.getenv("FALLBACK_MODEL_ID")
        self.sleep_fn = sleep_fn
        self.default_max_tokens = int(os.getenv("DEFAULT_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        self.escalated_max_tokens = int(
            os.getenv("ESCALATED_MAX_TOKENS", ESCALATED_MAX_TOKENS)
        )
        self.max_recovery_retries = int(
            os.getenv("MAX_RECOVERY_RETRIES", MAX_RECOVERY_RETRIES)
        )
        self.max_retries = int(os.getenv("MAX_RETRIES", MAX_RETRIES))

    def new_state(self) -> RecoveryState:
        return RecoveryState(
            current_model=self.primary_model,
            fallback_model=self.fallback_model,
            max_tokens=self.default_max_tokens,
        )

    def call_model(self, client, state: RecoveryState, *, system, messages, tools):
        return self.with_retry(
            lambda: client.messages.create(
                model=state.current_model,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=state.max_tokens,
            ),
            state,
        )

    def with_retry(self, fn, state: RecoveryState):
        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = fn()
                state.consecutive_529 = 0
                return response
            except Exception as exc:
                last_error = exc

                if self.is_rate_limit(exc):
                    delay = self.retry_delay(attempt, self.retry_after_seconds(exc))
                    print(
                        f"[429 rate limit] retry {attempt + 1}/{self.max_retries}, "
                        f"wait {delay:.1f}s"
                    )
                    self.sleep_fn(delay)
                    continue

                if self.is_overloaded(exc):
                    state.consecutive_529 += 1
                    if state.consecutive_529 >= MAX_CONSECUTIVE_529:
                        self.switch_to_fallback_model(state)

                    delay = self.retry_delay(attempt, self.retry_after_seconds(exc))
                    print(
                        f"[529 overloaded] retry {attempt + 1}/{self.max_retries}, "
                        f"wait {delay:.1f}s"
                    )
                    self.sleep_fn(delay)
                    continue

                raise

        raise RuntimeError(f"Max retries ({self.max_retries}) exceeded") from last_error

    def retry_delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return retry_after

        base = min(BASE_DELAY_MS * (2**attempt), 32000) / 1000
        return base + random.uniform(0, base * 0.25)

    def retry_after_seconds(self, exc: Exception) -> float | None:
        cause = getattr(exc, "__cause__", None)
        headers = getattr(cause, "headers", None)
        if not headers:
            return None

        value = headers.get("Retry-After") or headers.get("retry-after")
        if not value:
            return None

        try:
            return float(value)
        except ValueError:
            return None

    def switch_to_fallback_model(self, state: RecoveryState) -> None:
        if state.fallback_model:
            state.current_model = state.fallback_model
            print(f"[529 x{MAX_CONSECUTIVE_529}] switching to {state.fallback_model}")
        else:
            print(
                f"[529 x{MAX_CONSECUTIVE_529}] no FALLBACK_MODEL_ID configured; "
                "continuing retries"
            )
        state.consecutive_529 = 0

    def recover_max_tokens(self, response, messages: list, state: RecoveryState) -> str:
        if response.stop_reason != "max_tokens":
            return "not_max_tokens"

        if not state.has_escalated_tokens:
            print(
                f"[max_tokens] escalating "
                f"{state.max_tokens} -> {self.escalated_max_tokens}"
            )
            state.max_tokens = self.escalated_max_tokens
            state.has_escalated_tokens = True
            return "retry_same_request"

        messages.append({"role": "assistant", "content": response.content})
        if state.continuation_count < self.max_recovery_retries:
            state.continuation_count += 1
            messages.append({"role": "user", "content": CONTINUATION_PROMPT})
            print(
                f"[max_tokens] continuation "
                f"{state.continuation_count}/{self.max_recovery_retries}"
            )
            return "retry_with_messages"

        print("[max_tokens] recovery limit reached")
        return "stop"

    def is_rate_limit(self, exc: Exception) -> bool:
        text = self.error_text(exc)
        return "429" in text or "rate limit" in text or "ratelimit" in text

    def is_overloaded(self, exc: Exception) -> bool:
        text = self.error_text(exc)
        return "529" in text or "overloaded" in text

    def error_text(self, exc: Exception) -> str:
        return f"{type(exc).__name__}: {exc}".lower()
