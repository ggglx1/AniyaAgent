from __future__ import annotations

from main.conversation.service import ConversationMemoryService
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .classifier import CandidateClassifier
from .extractor import MemoryExtractor
from .manager import PersonalMemoryManager
from .policy import MemoryPolicy
from .resolver import MemoryResolver
from .llm_extractor import ControlledLlmMemoryExtractor
from .candidate_validator import CandidateValidator
from .processing_ledger import ProcessingLedger
from .intent_guard import IntentGuard
from .scopes import MemoryScopePolicy


class StructuredMemoryPipeline:
    def __init__(self, manager: PersonalMemoryManager, conversation: ConversationMemoryService, profile_store=None, personal_state=None, llm_extractor=None):
        self.manager = manager
        self.conversation = conversation
        self.profile_store = profile_store
        self.personal_state = personal_state
        self.extractor = MemoryExtractor()
        self.classifier = CandidateClassifier()
        self.policy = MemoryPolicy()
        self.resolver = MemoryResolver(manager)
        self.llm_extractor = llm_extractor or ControlledLlmMemoryExtractor()
        self.validator = CandidateValidator(conversation.repository)
        self.ledger = ProcessingLedger(manager.workdir)
        self.extractor_version = "memory-pipeline-v2"
        self.intent_guard = IntentGuard()

    def process(self, message_ids: list[str], user_id: str = "local", mode: str = "assistant", repository_id: str = "") -> list[str]:
        records = [item for item in self.conversation.repository.recent_messages(200) if item.message_id in set(message_ids)]
        records = [item for item in records if not self.ledger.processed(item.message_id, self.extractor_version)]
        if not records:
            return []
        created = []
        self.route_tasks_and_reminders(records)
        for raw in self.extractor.extract(records) + self.llm_extractor.extract(records):
            candidate = self.validator.validate(self.classifier.classify(raw), {item.message_id for item in records})
            if candidate is None:
                continue
            decision = self.policy.decide(candidate)
            scope = self.scope_for(mode, repository_id, candidate)
            candidate["scope"] = scope
            candidate["repository_id"] = repository_id
            if decision == "route_profile":
                self.update_profile(candidate)
                continue
            if decision not in {"write_active", "write_pending"}:
                continue
            resolution, existing = self.resolver.resolve(candidate, user_id)
            if resolution == "duplicate":
                continue
            if resolution == "conflict":
                record = self.manager.supersede(existing.id, candidate["content"], user_id=user_id, reason="new explicit conversation fact")
            else:
                record = self.manager.add_scoped(
                    content=candidate["content"], memory_type=candidate["memory_type"], user_id=user_id,
                    explicit=decision == "write_active", importance=candidate["importance"], confidence=candidate["confidence"],
                    tags=candidate["tags"], entity_refs=candidate["entity_refs"], source="conversation_explicit" if candidate["explicit"] else "conversation_inference",
                    origin=candidate["origin"], valid_until=candidate["valid_until"], metadata={"source_message_ids": candidate["source_message_ids"]}, reason="conversation memory pipeline", scope=scope, repository_id=repository_id,
                )
            self.conversation.repository.link_long_term_memory(record.id, candidate["source_message_ids"], "explicit_source" if candidate["explicit"] else "inferred_from")
            created.append(record.id)
        self.ledger.mark([item.message_id for item in records], self.extractor_version, self.manager.now_iso())
        return created

    def route_tasks_and_reminders(self, records: list) -> None:
        if self.personal_state is None:
            return
        timezone_name = str(self.profile_store.get().get("timezone") or "Asia/Shanghai") if self.profile_store else "Asia/Shanghai"
        for item in records:
            if item.role != "user":
                continue
            text = self.extractor.text(item.content).strip()
            if "提醒我" in text:
                content = text.split("提醒我", 1)[1].strip("：: ，,。") or text
                decision = self.intent_guard.decision(text, source_role=item.role, has_complete_time=self.has_explicit_reminder_time(text))
                if decision != "confirmed":
                    self.record_pending(item.message_id, "reminder", text, decision)
                    continue
                when = self.reminder_time(text, timezone_name)
                self.personal_state.create_reminder(content, when, timezone_name=timezone_name, target_channel="web")
            elif any(marker in text for marker in ("待办", "任务", "帮我安排")):
                decision = self.intent_guard.decision(text, source_role=item.role, require_action=True)
                if decision == "confirmed": self.personal_state.create_task(text[:300], source_conversation=item.message_id)
                else: self.record_pending(item.message_id, "task", text, decision)

    def reminder_time(self, text: str, timezone_name: str) -> str:
        now = datetime.now(ZoneInfo(timezone_name))
        target = now + timedelta(days=1) if "明天" in text else now + timedelta(hours=1)
        target = target.replace(hour=9 if "明天" in text else target.hour, minute=0, second=0, microsecond=0)
        return target.isoformat()

    def has_explicit_reminder_time(self, text: str) -> bool:
        return bool(__import__('re').search(r"(?:\d{1,2}[:：]\d{2}|\d{1,2}点|上午|下午|晚上|中午)", text))

    def record_pending(self, message_id: str, kind: str, text: str, decision: str) -> None:
        self.conversation.repository.request_maintenance("intent_candidate", {"message_id": message_id, "kind": kind, "raw_text": text[:500], "state": decision})

    def scope_for(self, mode: str, repository_id: str, candidate: dict) -> str:
        if mode == "coding": return MemoryScopePolicy.coding_project(repository_id) if repository_id else MemoryScopePolicy.CODING_GLOBAL
        return MemoryScopePolicy.ASSISTANT_ONLY

    def update_profile(self, candidate: dict) -> None:
        if self.profile_store is None:
            return
        # The deterministic extractor classifies only an explicit name statement as profile_fact.
        self.profile_store.update({"display_name": candidate["content"]}, source="conversation_memory_pipeline")
