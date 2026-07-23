from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from typing import Iterator


_request_context: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "aniya_llm_request_context",
    default={},
)


def current_request_context() -> dict:
    return dict(_request_context.get() or {})


@contextmanager
def bind_request_context(values: dict) -> Iterator[None]:
    token = _request_context.set(dict(values or {}))
    try:
        yield
    finally:
        _request_context.reset(token)


def normalize_usage(raw: dict | None, provider: str = "") -> dict:
    usage = dict((raw or {}).get("usage") or {})
    if provider == "anthropic" or "input_tokens" in usage:
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cached_input_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
        reasoning_tokens = int(usage.get("reasoning_tokens", 0) or 0)
    else:
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        cached_input_tokens = int(
            details.get("cached_tokens", usage.get("cached_tokens", 0)) or 0
        )
        cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
        reasoning_tokens = int(completion_details.get("reasoning_tokens", 0) or 0)

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(usage.get("total_tokens", input_tokens + output_tokens) or 0),
        "cached_input_tokens": cached_input_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "reasoning_tokens": reasoning_tokens,
        "raw_usage": usage,
    }


def estimate_tokens(value) -> int:
    """Provider-neutral attribution estimate, not a billing value."""
    if value is None:
        return 0
    return max(0, (len(str(value)) + 3) // 4)


def request_timing(started_at: float, queued_at: float) -> dict:
    now = time.perf_counter()
    return {
        "queue_wait_ms": round(max(0.0, (started_at - queued_at) * 1000), 2),
        "provider_duration_ms": round(max(0.0, (now - started_at) * 1000), 2),
    }
