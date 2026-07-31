from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from main.actions.parser import StructuredCommandParser


@dataclass(frozen=True)
class TemporalResolution:
    value: str = ""
    issue: str = ""
    reference_at: str = ""
    version: str = "temporal-v1"


class TemporalResolver:
    incomplete_markers = ("\u4e0b\u5468", "\u4e0b\u5348", "\u665a\u70b9", "\u8fc7\u4f1a\u513f", "\u7a0d\u540e", "later", "next week")

    def __init__(self): self.parser = StructuredCommandParser()

    def resolve(self, text: str, timezone_name: str, value: str = "") -> TemporalResolution:
        reference = datetime.now().astimezone().isoformat()
        resolved = value or self.parser.parse_time(text, timezone_name)
        if not resolved:
            if any(marker in text.casefold() for marker in self.incomplete_markers):
                return TemporalResolution(issue="time_incomplete", reference_at=reference)
            return TemporalResolution(issue="time_missing", reference_at=reference)
        try:
            parsed = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
            if parsed.tzinfo is None: return TemporalResolution(issue="timezone_missing", reference_at=reference)
            if parsed <= datetime.now(parsed.tzinfo): return TemporalResolution(issue="time_in_past", reference_at=reference)
        except ValueError:
            return TemporalResolution(issue="time_invalid", reference_at=reference)
        return TemporalResolution(value=resolved, reference_at=reference)
