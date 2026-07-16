import json


class RoutineTool:
    def json(self, value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)


class CreateRoutineTool(RoutineTool):
    name = "create_routine"
    definition = {
        "name": name,
        "description": (
            "Create an explicit scheduled assistant routine. Never create one silently; the user must "
            "request the recurring behavior. Cron uses five fields."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "routine_type": {"type": "string", "enum": ["morning_plan", "evening_review", "weekly_review"]},
                "cron": {"type": "string"}, "timezone_name": {"type": "string"},
                "target_channel": {"type": "string"}, "enabled": {"type": "boolean"},
            },
            "required": ["name", "routine_type", "cron"],
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, name: str, routine_type: str, cron: str, timezone_name: str = "Asia/Shanghai",
            target_channel: str = "web", enabled: bool = True) -> str:
        return self.json(self.manager.create(
            name, routine_type, cron, timezone_name, target_channel, enabled,
        ).to_dict())


class ListRoutinesTool(RoutineTool):
    name = "list_routines"
    definition = {
        "name": name,
        "description": "List configured assistant routines, schedules, channels, and latest results.",
        "input_schema": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}, "limit": {"type": "integer"}},
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, enabled: bool | None = None, limit: int = 100) -> str:
        return self.json([item.to_dict() for item in self.manager.list(enabled, limit)])


class UpdateRoutineTool(RoutineTool):
    name = "update_routine"
    definition = {
        "name": name,
        "description": "Edit, pause, resume, reschedule, or redirect an existing routine.",
        "input_schema": {
            "type": "object",
            "properties": {
                "routine_id": {"type": "string"},
                "changes": {"type": "object", "additionalProperties": True},
            },
            "required": ["routine_id", "changes"],
        },
    }

    def __init__(self, manager): self.manager = manager
    def run(self, routine_id: str, changes: dict) -> str:
        return self.json(self.manager.update(routine_id, changes).to_dict())


class RunRoutineNowTool(RoutineTool):
    name = "run_routine_now"
    definition = {
        "name": name,
        "description": "Run one configured routine immediately because the user explicitly requested it.",
        "input_schema": {
            "type": "object", "properties": {"routine_id": {"type": "string"}},
            "required": ["routine_id"],
        },
    }

    def __init__(self, dispatcher): self.dispatcher = dispatcher
    def run(self, routine_id: str) -> str:
        return self.json(self.dispatcher.run_now(routine_id))


def build_routine_tools(manager, dispatcher=None) -> list:
    tools = [CreateRoutineTool(manager), ListRoutinesTool(manager), UpdateRoutineTool(manager)]
    if dispatcher is not None:
        tools.append(RunRoutineNowTool(dispatcher))
    return tools
