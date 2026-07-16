from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import PersonalRoutine, RoutineType
from .repository import PersonalStateRepository
from .scheduling import CronSchedule


class RoutineManager:
    allowed_fields = {"name", "routine_type", "cron", "timezone", "target_channel", "enabled"}

    def __init__(
        self,
        workdir: Path,
        user_id: str = "local",
        repository: PersonalStateRepository | None = None,
    ):
        self.workdir = workdir.resolve()
        self.user_id = user_id
        self.repository = repository or PersonalStateRepository(self.workdir)
        self.workspace_dir = self.workdir / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        name: str,
        routine_type: str,
        cron: str,
        timezone_name: str = "Asia/Shanghai",
        target_channel: str = "web",
        enabled: bool = True,
    ) -> PersonalRoutine:
        self.validate_name(name)
        self.validate_type(routine_type)
        CronSchedule.validate(cron)
        ZoneInfo(timezone_name)
        now = self.now_iso()
        routine = PersonalRoutine(
            id=self.new_id(), user_id=self.user_id, name=name.strip(),
            routine_type=routine_type, cron=cron.strip(), timezone=timezone_name,
            target_channel=target_channel or "web", enabled=bool(enabled),
            created_at=now, updated_at=now,
        )
        self.repository.insert(
            "personal_routines", routine,
            self.activity(routine.id, "created", None, routine),
        )
        self.sync_workspace()
        return routine

    def update(self, routine_id: str, changes: dict, source: str = "user") -> PersonalRoutine:
        before = self.require(routine_id)
        if not changes:
            raise ValueError("At least one routine change is required")
        invalid = sorted(set(changes) - self.allowed_fields)
        if invalid:
            raise ValueError(f"Unsupported routine fields: {', '.join(invalid)}")
        clean = dict(changes)
        if "name" in clean:
            self.validate_name(clean["name"])
            clean["name"] = clean["name"].strip()
        if "routine_type" in clean:
            self.validate_type(clean["routine_type"])
        if "cron" in clean:
            CronSchedule.validate(clean["cron"])
            clean["cron"] = clean["cron"].strip()
        if "timezone" in clean:
            ZoneInfo(clean["timezone"])
        if "enabled" in clean:
            clean["enabled"] = bool(clean["enabled"])
        after = replace(before, **clean, updated_at=self.now_iso())
        self.repository.update(
            "personal_routines", before, after,
            self.activity(routine_id, "updated", before, after, source),
        )
        self.sync_workspace()
        return after

    def record_run(self, routine_id: str, success: bool, result: str, run_at: datetime) -> PersonalRoutine:
        before = self.require(routine_id)
        after = replace(
            before,
            last_run_at=self.iso(run_at),
            last_result=("success: " if success else "failed: ") + result[:500],
            updated_at=self.now_iso(),
        )
        self.repository.update(
            "personal_routines", before, after,
            self.activity(routine_id, "ran", before, after, "routine_dispatcher"),
        )
        self.sync_workspace()
        return after

    def list(self, enabled: bool | None = None, limit: int = 100) -> list[PersonalRoutine]:
        return self.repository.list_routines(self.user_id, enabled, limit)

    def due(self, now: datetime | None = None) -> list[PersonalRoutine]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        due = []
        for routine in self.list(enabled=True, limit=500):
            local_now = current.astimezone(ZoneInfo(routine.timezone))
            if not CronSchedule.matches(routine.cron, local_now):
                continue
            last_run = self.parse(routine.last_run_at)
            if last_run:
                last_marker = last_run.astimezone(ZoneInfo(routine.timezone)).strftime("%Y-%m-%d %H:%M")
                if last_marker == local_now.strftime("%Y-%m-%d %H:%M"):
                    continue
            due.append(routine)
        return due

    def require(self, routine_id: str) -> PersonalRoutine:
        routine = self.repository.get("personal_routines", PersonalRoutine, routine_id, self.user_id)
        if routine is None:
            raise FileNotFoundError(f"Routine not found: {routine_id}")
        return routine

    def sync_workspace(self) -> None:
        lines = ["# Routines", "", "Generated from structured personal state.", ""]
        for routine in self.list(limit=500):
            state = "enabled" if routine.enabled else "paused"
            lines.append(
                f"- [{state}] {routine.name} ({routine.routine_type}) "
                f"`{routine.cron}` {routine.timezone} -> {routine.target_channel} <!-- {routine.id} -->"
            )
        if len(lines) == 4:
            lines.append("(none)")
        path = self.workspace_dir / "ROUTINES.md"
        temp = path.with_suffix(".md.tmp")
        temp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        temp.replace(path)

    def activity(self, routine_id: str, operation: str, before, after, source: str = "user") -> dict:
        return {
            "id": f"act_{uuid.uuid4().hex[:16]}", "user_id": self.user_id,
            "entity_type": "routine", "entity_id": routine_id, "operation": operation,
            "before": before.to_dict() if before else None,
            "after": after.to_dict() if after else None,
            "source": source, "created_at": self.now_iso(),
        }

    def validate_name(self, name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Routine name cannot be empty")

    def validate_type(self, routine_type: str) -> None:
        if routine_type not in {item.value for item in RoutineType}:
            raise ValueError(f"Invalid routine type: {routine_type}")

    def new_id(self) -> str:
        return f"routine_{uuid.uuid4().hex[:16]}"

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def iso(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def parse(self, value: str) -> datetime | None:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
