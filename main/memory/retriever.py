import re

from .manager import PersonalMemoryManager
from .models import MemoryRecord


class PersonalMemoryRetriever:
    def __init__(self, manager: PersonalMemoryManager):
        self.manager = manager

    def retrieve(self, query: str, user_id: str = "local", limit: int = 5) -> list[MemoryRecord]:
        candidates = self.manager.search(query, user_id=user_id, limit=max(limit * 4, 20))
        terms = {term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1}

        def score(record: MemoryRecord) -> float:
            content = record.content.lower()
            lexical = sum(1 for term in terms if term in content)
            return lexical * 2 + record.importance * 2 + record.confidence

        return sorted(candidates, key=score, reverse=True)[:limit]

    def context(self, query: str, user_id: str = "local", limit: int = 5) -> str:
        records = self.retrieve(query, user_id=user_id, limit=limit)
        if not records:
            return ""
        lines = ["<approved_personal_memories>"]
        for record in records:
            lines.append(
                f"- [{record.id}] ({record.type}, confidence={record.confidence:.2f}) {record.content}"
            )
        lines.append("</approved_personal_memories>")
        return "\n".join(lines)
