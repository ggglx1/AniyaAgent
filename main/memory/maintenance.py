from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


class MemoryMaintenanceService:
    """Idempotent daily/weekly maintenance triggered by Web activity, never by an LLM call."""

    def __init__(self, workdir: Path, conversation, consolidator, planner, profile):
        self.workdir = workdir.resolve()
        self.conversation = conversation
        self.consolidator = consolidator
        self.planner = planner
        self.profile = profile
        self.state_path = self.workdir / ".conversation" / "maintenance.json"
        self.stop_event = threading.Event()
        self.started = False

    def start(self, interval_seconds: int = 60) -> None:
        if self.started:
            return
        self.started = True
        threading.Thread(target=self.run, args=(max(10, interval_seconds),), daemon=True, name="daily-memory-maintenance").start()

    def run(self, interval_seconds: int) -> None:
        while not self.stop_event.wait(interval_seconds):
            try:
                self.tick()
            except Exception:
                # Per-day failures are persisted by run_daily; scheduler must remain alive.
                pass

    def stop(self) -> None:
        self.stop_event.set()

    def tick(self) -> dict:
        timezone_name = str(self.profile.get().get("timezone") or "Asia/Shanghai")
        local_now = datetime.now(ZoneInfo(timezone_name))
        rebuilt = self.conversation.rebuild_prior_days(timezone_name)
        generated_today = ""
        if local_now.hour >= 23:
            generated_today = self.run_daily(local_now.date().isoformat())
        state = self.load_state()
        week_key = f"{local_now.isocalendar().year}-W{local_now.isocalendar().week:02d}"
        proposal_id = ""
        archived = self.archive_expired_memories()
        # Run one reflection proposal per ISO week, after the week has begun.
        if state.get("last_weekly_reflection") != week_key and local_now.weekday() == 0:
            review = self.planner.weekly_review(local_now)
            source_ids = self.source_ids_since((local_now - timedelta(days=7)).date().isoformat())
            proposal_id = self.consolidator.weekly_reflection(
                "Weekly review proposal: " + json.dumps(review, ensure_ascii=False, default=str), source_ids
            )
            state["last_weekly_reflection"] = week_key
        state["last_tick_at"] = local_now.isoformat()
        self.save_state(state)
        return {"rebuilt_daily_memories": rebuilt, "generated_today": generated_today, "weekly_reflection_id": proposal_id, "archived_memory_ids": archived}

    def run_daily(self, local_date: str) -> str:
        try:
            return self.conversation.generate_daily_memory(local_date)
        except Exception as exc:
            self.conversation.repository.mark_daily_failed(local_date, f"{type(exc).__name__}: {exc}")
            return ""

    def archive_expired_memories(self) -> list[str]:
        now = datetime.now(timezone.utc)
        archived = []
        for record in self.consolidator.manager.list(status="active", limit=500):
            expires_at = self.parse_datetime(record.valid_until)
            if expires_at and expires_at <= now:
                self.consolidator.manager.archive(record.id, reason="memory retention period expired")
                archived.append(record.id)
        return archived

    def parse_datetime(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    def source_ids_since(self, local_date: str) -> list[str]:
        return [item["message_id"] for item in self.conversation.repository.export() if item["day_date"] >= local_date and not item["redacted_at"]]

    def load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.state_path)
