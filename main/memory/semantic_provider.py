from __future__ import annotations

from typing import Protocol


class SemanticSearchProvider(Protocol):
    """Optional provider. Retrieval must keep working when it is absent or unavailable."""

    def search(self, query: str, user_id: str, limit: int) -> list[str]:
        ...
