from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class CronSchedule:
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]

    @classmethod
    def validate(cls, expression: str) -> None:
        fields = expression.strip().split()
        if len(fields) != 5:
            raise ValueError("Recurrence must be a five-field cron expression")
        for field, (minimum, maximum) in zip(fields, cls.ranges):
            for part in field.split(","):
                cls.validate_part(part, minimum, maximum)

    @classmethod
    def next_after(cls, expression: str, after: datetime, timezone_name: str) -> str:
        cls.validate(expression)
        zone = ZoneInfo(timezone_name)
        current = after.astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):
            if cls.matches(expression, current):
                return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            current += timedelta(minutes=1)
        raise ValueError("Could not find the next recurrence within one year")

    @classmethod
    def matches(cls, expression: str, value: datetime) -> bool:
        minute, hour, day, month, weekday = expression.strip().split()
        weekday_value = (value.weekday() + 1) % 7
        if not cls.field_matches(minute, value.minute):
            return False
        if not cls.field_matches(hour, value.hour):
            return False
        if not cls.field_matches(month, value.month):
            return False
        day_ok = cls.field_matches(day, value.day)
        weekday_ok = cls.field_matches(weekday, weekday_value)
        if day == "*" and weekday == "*":
            return True
        if day == "*":
            return weekday_ok
        if weekday == "*":
            return day_ok
        return day_ok or weekday_ok

    @classmethod
    def field_matches(cls, expression: str, value: int) -> bool:
        return any(cls.part_matches(part, value) for part in expression.split(","))

    @classmethod
    def part_matches(cls, part: str, value: int) -> bool:
        if part == "*":
            return True
        if part.startswith("*/"):
            return value % int(part[2:]) == 0
        if "-" in part:
            start, end = part.split("-", 1)
            return int(start) <= value <= int(end)
        return int(part) == value or (value == 0 and part == "7")

    @classmethod
    def validate_part(cls, part: str, minimum: int, maximum: int) -> None:
        try:
            if part == "*":
                return
            if part.startswith("*/"):
                if int(part[2:]) <= 0:
                    raise ValueError
                return
            if "-" in part:
                start, end = (int(item) for item in part.split("-", 1))
                if start > end or start < minimum or end > maximum:
                    raise ValueError
                return
            value = int(part)
            if value < minimum or value > maximum:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"Invalid cron field: {part}") from exc
