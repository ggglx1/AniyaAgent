from __future__ import annotations


class MemoryScopePolicy:
    """Visibility policy for stable long-term memory across product modes."""

    GLOBAL_PERSONAL = "global_personal"
    ASSISTANT_ONLY = "assistant_only"
    CODING_GLOBAL = "coding_global"

    @staticmethod
    def coding_project(repository_id: str) -> str:
        if not repository_id: raise ValueError("repository_id is required for project memory")
        return f"coding_project:{repository_id}"

    @staticmethod
    def allowed(scope: str, mode: str, repository_id: str = "") -> bool:
        if mode == "qa": return False
        if scope == MemoryScopePolicy.GLOBAL_PERSONAL: return mode in {"assistant", "coding"}
        if scope == MemoryScopePolicy.ASSISTANT_ONLY: return mode == "assistant"
        if scope == MemoryScopePolicy.CODING_GLOBAL: return mode == "coding"
        return mode == "coding" and scope == MemoryScopePolicy.coding_project(repository_id)
