from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    mode: Literal["assistant", "coding", "qa"] = "assistant"
    topic_id: str = Field(default="", max_length=128)
    repository_root: str = Field(default="", max_length=4096)
    work_session_id: str = Field(default="", max_length=128)
    files: list[dict[str, Any]] = Field(default_factory=list)
    images: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)


class TrackRequest(BaseModel):
    mode: Literal["assistant", "coding", "qa"]
    topic_id: str = Field(default="", max_length=128)
    repository_root: str = Field(default="", max_length=4096)
    work_session_id: str = Field(default="", max_length=128)
    force_new: bool = False


class PermissionRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    allow: bool


class ProviderRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)


class RedactRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)


class MemoryActionRequest(BaseModel):
    memory_id: str = Field(min_length=1, max_length=128)
    action: Literal["confirm", "correct", "archive", "forget"]
    content: str = Field(default="", max_length=20_000)


class WeixinBindingRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    recipient_id: str = Field(min_length=1, max_length=256)
    context_token: str = Field(min_length=1, max_length=4096)
