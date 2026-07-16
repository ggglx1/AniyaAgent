import json
import threading
import uuid
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from main.channel.base import AgentResponse

from .proactive_engine import ProactiveEngine


class RoutineDispatcher:
    def __init__(
        self,
        workdir: Path,
        routines,
        planner,
        profile,
        channel_registry,
        interval_seconds: int = 30,
    ):
        self.workdir = workdir.resolve()
        self.routines = routines
        self.planner = planner
        self.profile = profile
        self.channel_registry = channel_registry
        self.interval_seconds = max(1, interval_seconds)
        self.outbox = self.workdir / ".personal" / "routine_outbox.jsonl"
        self.engine = ProactiveEngine()
        self.started = False
        self.stop_event = threading.Event()
        self.execution_lock = threading.Lock()

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        threading.Thread(target=self.run, daemon=True, name="personal-routine-dispatcher").start()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                self.tick()
            except Exception as exc:
                self.append_outbox({"event": "routine.dispatcher.error", "error": f"{type(exc).__name__}: {exc}"})

    def tick(self, now: datetime | None = None) -> int:
        with self.execution_lock:
            current = self.utc_now(now)
            profile = self.profile.get()
            local_now = current.astimezone(ZoneInfo(profile.get("timezone") or "Asia/Shanghai"))
            quiet_start, quiet_end = self.quiet_hours(profile.get("quiet_hours") or {})
            ran = 0
            for routine in self.routines.due(current):
                decision = self.engine.decide(
                    local_now,
                    quiet_start=quiet_start,
                    quiet_end=quiet_end,
                    routines=[{**routine.to_dict(), "due": True}],
                    proactive_paused=bool(profile.get("proactive_paused")),
                )
                if decision.action == "run_routine":
                    self.execute(routine, current)
                    ran += 1
            return ran

    def run_now(self, routine_id: str, now: datetime | None = None) -> dict:
        with self.execution_lock:
            routine = self.routines.require(routine_id)
            return self.execute(routine, self.utc_now(now))

    def execute(self, routine, run_at: datetime) -> dict:
        try:
            output = self.generate(routine.routine_type, run_at)
            response = AgentResponse(
                channel_id=routine.target_channel,
                conversation_id=routine.user_id,
                run_id=f"routine_{uuid.uuid4().hex[:12]}",
                status="notification",
                text=self.render(routine.name, routine.routine_type, output),
                metadata={"routine_id": routine.id, "routine_type": routine.routine_type},
            )
            send_result = self.channel_registry.send(response)
            success = bool(send_result.ok)
            message = send_result.message
        except Exception as exc:
            output = {}
            success = False
            message = f"{type(exc).__name__}: {exc}"

        self.routines.record_run(routine.id, success, message, run_at)
        event = {
            "event": "routine.run", "routine_id": routine.id,
            "routine_type": routine.routine_type, "success": success,
            "result": message, "created_at": self.iso(run_at),
        }
        self.append_outbox(event)
        return {**event, "output": output}

    def generate(self, routine_type: str, now: datetime) -> dict:
        if routine_type == "morning_plan":
            return self.planner.morning_plan(now)
        if routine_type == "evening_review":
            return self.planner.evening_review(now)
        if routine_type == "weekly_review":
            return self.planner.weekly_review(now)
        raise ValueError(f"Unsupported routine type: {routine_type}")

    def render(self, name: str, routine_type: str, output: dict) -> str:
        return (
            f"{name} ({routine_type})\n"
            f"{json.dumps(output, ensure_ascii=False, indent=2)}"
        )

    def quiet_hours(self, value: dict) -> tuple[clock_time | None, clock_time | None]:
        try:
            return (
                clock_time.fromisoformat(str(value.get("start") or "")),
                clock_time.fromisoformat(str(value.get("end") or "")),
            )
        except ValueError:
            return None, None

    def append_outbox(self, event: dict) -> None:
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        with self.outbox.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def utc_now(self, value: datetime | None) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def iso(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
