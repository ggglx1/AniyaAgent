import json


class DailyTool:
    def json(self, value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)


class GetTodayOverviewTool(DailyTool):
    name = "get_today_overview"
    definition = {
        "name": name,
        "description": "Read today's structured tasks, reminders, projects, overdue items, and completed work.",
        "input_schema": {"type": "object", "properties": {}},
    }

    def __init__(self, planner): self.planner = planner
    def run(self) -> str: return self.json(self.planner.today_overview())


class GenerateMorningPlanTool(DailyTool):
    name = "generate_morning_plan"
    definition = {
        "name": name,
        "description": (
            "Generate a focused morning plan from real personal state. The plan is advisory and does not "
            "silently change tasks or create commitments."
        ),
        "input_schema": {"type": "object", "properties": {}},
    }

    def __init__(self, planner): self.planner = planner
    def run(self) -> str: return self.json(self.planner.morning_plan())


class GenerateEveningReviewTool(DailyTool):
    name = "generate_evening_review"
    definition = {
        "name": name,
        "description": (
            "Generate an evening review from today's actual completions, unfinished due work, reminders, "
            "stale items, and active projects."
        ),
        "input_schema": {"type": "object", "properties": {}},
    }

    def __init__(self, planner): self.planner = planner
    def run(self) -> str: return self.json(self.planner.evening_review())


class GenerateWeeklyReviewTool(DailyTool):
    name = "generate_weekly_review"
    definition = {
        "name": name,
        "description": "Generate a factual seven-day review from tasks and active project state.",
        "input_schema": {"type": "object", "properties": {}},
    }

    def __init__(self, planner): self.planner = planner
    def run(self) -> str: return self.json(self.planner.weekly_review())


def build_daily_tools(planner) -> list:
    return [
        GetTodayOverviewTool(planner),
        GenerateMorningPlanTool(planner),
        GenerateEveningReviewTool(planner),
        GenerateWeeklyReviewTool(planner),
    ]
