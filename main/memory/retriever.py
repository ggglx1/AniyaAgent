import re
from datetime import datetime, timezone

from .manager import PersonalMemoryManager
from .models import MemoryRecord
from .semantic_provider import SemanticSearchProvider


class PersonalMemoryRetriever:
    def __init__(self, manager: PersonalMemoryManager, semantic_provider: SemanticSearchProvider | None = None):
        self.manager = manager
        self.semantic_provider = semantic_provider
        self.last_retrieved_ids: list[str] = []

    def retrieve(self, query: str, user_id: str = "local", limit: int = 5) -> list[MemoryRecord]:
        terms = self.terms(query)
        candidates = self.manager.repository.search_lexical(user_id, terms, limit=max(limit * 12, 120))
        if not candidates:
            # A small, approved memory set remains useful when wording has no lexical overlap.
            candidates = self.manager.list(user_id=user_id, status="active", limit=max(limit * 8, 100))
        if self.semantic_provider is not None:
            try:
                semantic_ids = set(self.semantic_provider.search(query, user_id, max(limit * 4, 20)))
                known = {record.id for record in candidates}
                candidates.extend(record for record in self.manager.list(user_id=user_id, status="active", limit=500) if record.id in semantic_ids and record.id not in known)
            except Exception:
                pass
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        def score(record: MemoryRecord) -> float:
            if record.valid_until and record.valid_until < now:
                return -999
            content = f"{record.content} {' '.join(record.tags)} {' '.join(record.entity_refs)}".lower()
            lexical = sum(1 for term in terms if term in content)
            chinese_overlap = sum(1 for term in terms if len(term) >= 2 and term in content)
            recency = 0.5 if record.last_accessed_at else 0
            goal_relevance = 1.0 if record.type == "goal" and any(word in query for word in ("目标", "计划", "长期")) else 0.0
            return lexical * 3 + chinese_overlap + record.importance * 2 + record.confidence + recency + goal_relevance

        selected = [record for record in sorted(candidates, key=score, reverse=True) if score(record) >= 0][:limit]
        self.last_retrieved_ids = [record.id for record in selected]
        self.manager.repository.mark_accessed(selected, self.manager.now_iso())
        return selected

    def context(self, query: str, user_id: str = "local", limit: int = 5) -> str:
        records = self.retrieve(query, user_id=user_id, limit=limit)
        if not records:
            return ""
        lines = ["<approved_long_term_memories>"]
        for record in records:
            lines.append(
                f"- [{record.id}] ({record.type}, confidence={record.confidence:.2f}) {record.content}"
            )
        lines.append("</approved_long_term_memories>")
        return "\n".join(lines)

    def terms(self, query: str) -> set[str]:
        tokens = {term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1}
        compact = re.sub(r"\s+", "", query.lower())
        tokens.update(compact[index:index + 2] for index in range(max(0, len(compact) - 1)) if re.search(r"[\u4e00-\u9fff]", compact[index:index + 2]))
        return tokens
