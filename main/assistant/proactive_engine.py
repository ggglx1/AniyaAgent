from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True)
class ProactiveDecision:
    action: str
    reason: str
    payload: dict


class ProactiveEngine:
    """Deterministic first-pass policy. LLM generation happens only after an action is selected."""

    def decide(
        self,
        now: datetime,
        quiet_start: time | None = None,
        quiet_end: time | None = None,
        due_reminders: list[dict] | None = None,
        routines: list[dict] | None = None,
        proactive_paused: bool = False,
    ) -> ProactiveDecision:
        if proactive_paused:
            return ProactiveDecision("none", "proactive messages are paused", {})
        if self.in_quiet_hours(now.time(), quiet_start, quiet_end):
            return ProactiveDecision("none", "current time is inside quiet hours", {})
        if due_reminders:
            return ProactiveDecision("deliver_reminder", "a reminder is due", due_reminders[0])
        for routine in routines or []:
            if routine.get("enabled") and routine.get("due"):
                return ProactiveDecision("run_routine", "a configured routine is due", routine)
        return ProactiveDecision("none", "no useful proactive action is due", {})

    def in_quiet_hours(
        self,
        current: time,
        start: time | None,
        end: time | None,
    ) -> bool:
        if start is None or end is None or start == end:
            return False
        if start < end:
            return start <= current < end
        return current >= start or current < end
