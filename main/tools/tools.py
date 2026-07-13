import glob as glob_lib
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from main.teams.agent_teams import AgentTeams
from main.tasks.background_tasks import BackgroundTasks
from main.tasks.cron_scheduler import CronScheduler
from main.tasks.task_system import TaskSystem
from main.tools.tool_result import ToolCallValidator, ToolResult
from main.tools.personal_tools import build_personal_tools
from main.tools.personal_state_tools import build_personal_state_tools
from main.tools.daily_tools import build_daily_tools
from main.teams.worktree_manager import WorktreeManager


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def definition(self) -> dict:
        pass

    @abstractmethod
    def run(self, **kwargs) -> str:
        pass


class WorkspaceTool(Tool):
    def __init__(self, workdir: Path, workdir_provider=None):
        self.base_workdir = workdir.resolve()
        self.workdir_provider = workdir_provider

    @property
    def workdir(self) -> Path:
        if self.workdir_provider is None:
            return self.base_workdir
        return Path(self.workdir_provider()).resolve()

    def safe_path(self, path: str) -> Path:
        workdir = self.workdir
        target = (workdir / path).resolve()
        try:
            target.relative_to(workdir)
        except ValueError:
            raise ValueError(f"Path escapes workspace: {path}")
        return target


class BashTool(WorkspaceTool):
    @property
    def name(self) -> str:
        return "bash"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Run a shell command.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "run_in_background": {"type": "boolean"},
                },
                "required": ["command"],
            },
        }

    def run(self, command: str, run_in_background: bool = False) -> str:
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
        if any(item in command for item in dangerous):
            return "Error: Dangerous command blocked"

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            output = (result.stdout + result.stderr).strip()
            return output[:50000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (120s)"
        except (FileNotFoundError, OSError) as exc:
            return f"Error: {exc}"


class ReadFileTool(WorkspaceTool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Read file contents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        }

    def run(self, path: str, limit: int = None) -> str:
        try:
            lines = self.safe_path(path).read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
            if limit and len(lines) > limit:
                hidden = len(lines) - limit
                lines = lines[:limit] + [f"... ({hidden} more lines)"]
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


class WriteFileTool(WorkspaceTool):
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Write content to a file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        }

    def run(self, path: str, content: str) -> str:
        try:
            file_path = self.safe_path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} chars to {path}"
        except Exception as exc:
            return f"Error: {exc}"


class EditFileTool(WorkspaceTool):
    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Replace exact text in a file once.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        }

    def run(self, path: str, old_text: str, new_text: str) -> str:
        try:
            file_path = self.safe_path(path)
            text = file_path.read_text(encoding="utf-8", errors="replace")

            if old_text not in text:
                return f"Error: text not found in {path}"

            file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
            return f"Edited {path}"
        except Exception as exc:
            return f"Error: {exc}"


class GlobTool(WorkspaceTool):
    @property
    def name(self) -> str:
        return "glob"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Find files matching a glob pattern.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                },
                "required": ["pattern"],
            },
        }

    def run(self, pattern: str) -> str:
        try:
            matches = []
            search_pattern = str(self.workdir / pattern)
            for match in glob_lib.glob(search_pattern, recursive=True):
                path = Path(match).resolve()
                try:
                    path.relative_to(self.workdir)
                except ValueError:
                    continue
                matches.append(str(path.relative_to(self.workdir)))

            return "\n".join(matches) if matches else "(no matches)"
        except Exception as exc:
            return f"Error: {exc}"


class TodoWriteTool(Tool):
    def __init__(self):
        self.current_todos = []

    @property
    def name(self) -> str:
        return "todo_write"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Create or update the current task checklist.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        }

    def run(self, todos: list) -> str:
        self.current_todos = todos

        print("\nCurrent Todos")
        for index, todo in enumerate(self.current_todos, start=1):
            status = todo.get("status", "pending")
            content = todo.get("content", "")
            print(f"{index}. [{status}] {content}")

        return f"Updated {len(self.current_todos)} todos"


class TaskTool(Tool):
    def __init__(self, task_runner):
        self.task_runner = task_runner

    @property
    def name(self) -> str:
        return "task"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": (
                "Launch a subagent to handle a complex subtask. "
                "Returns only the final conclusion."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                },
                "required": ["description"],
            },
        }

    def run(self, description: str) -> str:
        return self.task_runner(description)


class LoadSkillTool(Tool):
    def __init__(self, skill_loader):
        self.skill_loader = skill_loader

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Load the full content of a skill by name.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        }

    def run(self, name: str) -> str:
        return self.skill_loader(name)


class CompactTool(Tool):
    @property
    def name(self) -> str:
        return "compact"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Summarize earlier conversation to free context space.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "focus": {"type": "string"},
                },
            },
        }

    def run(self, focus: str = "") -> str:
        return "Compaction requested"


class CreateTaskTool(Tool):
    def __init__(self, task_system: TaskSystem):
        self.task_system = task_system

    @property
    def name(self) -> str:
        return "create_task"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Create a persistent task with optional blockedBy dependencies.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                    "blockedBy": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["subject"],
            },
        }

    def run(
        self,
        subject: str,
        description: str = "",
        blockedBy: list[str] | None = None,
    ) -> str:
        return self.task_system.create_task(subject, description, blockedBy)


class ListTasksTool(Tool):
    def __init__(self, task_system: TaskSystem):
        self.task_system = task_system

    @property
    def name(self) -> str:
        return "list_tasks"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "List all persistent tasks with status, owner, and dependencies.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    def run(self) -> str:
        return self.task_system.list_tasks()


class GetTaskTool(Tool):
    def __init__(self, task_system: TaskSystem):
        self.task_system = task_system

    @property
    def name(self) -> str:
        return "get_task"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Get full JSON details for a persistent task.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        }

    def run(self, task_id: str) -> str:
        return self.task_system.get_task(task_id)


class ClaimTaskTool(Tool):
    def __init__(self, task_system: TaskSystem, after_claim=None):
        self.task_system = task_system
        self.after_claim = after_claim

    @property
    def name(self) -> str:
        return "claim_task"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": (
                "Claim a pending persistent task. Refuses tasks whose blockedBy "
                "dependencies are not completed."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "owner": {"type": "string"},
                },
                "required": ["task_id"],
            },
        }

    def run(self, task_id: str, owner: str = "agent") -> str:
        result = self.task_system.claim_task(task_id, owner)
        if result.startswith("Claimed") and self.after_claim is not None:
            note = self.after_claim(task_id, owner)
            if note:
                result = f"{result}\n{note}"
        return result


class CompleteTaskTool(Tool):
    def __init__(self, task_system: TaskSystem, after_complete=None):
        self.task_system = task_system
        self.after_complete = after_complete

    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Mark an in-progress task completed and report newly unblocked tasks.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        }

    def run(self, task_id: str) -> str:
        result = self.task_system.complete_task(task_id)
        if result.startswith("Completed") and self.after_complete is not None:
            note = self.after_complete(task_id)
            if note:
                result = f"{result}\n{note}"
        return result


class ScheduleCronTool(Tool):
    def __init__(self, scheduler: CronScheduler):
        self.scheduler = scheduler

    @property
    def name(self) -> str:
        return "schedule_cron"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Schedule a prompt using a five-field cron expression.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "cron": {"type": "string"},
                    "prompt": {"type": "string"},
                    "recurring": {"type": "boolean"},
                    "durable": {"type": "boolean"},
                    "target_channel": {
                        "type": "string",
                        "description": "Channel id to deliver the scheduled result to, e.g. web, cli, cron.",
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": "Conversation id within the target channel.",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User id that owns this scheduled task.",
                    },
                },
                "required": ["cron", "prompt"],
            },
        }

    def run(
        self,
        cron: str,
        prompt: str,
        recurring: bool = True,
        durable: bool = True,
        target_channel: str = "cron",
        conversation_id: str = "scheduled",
        user_id: str = "scheduler",
    ) -> str:
        return self.scheduler.schedule(
            cron,
            prompt,
            recurring,
            durable,
            target_channel,
            conversation_id,
            user_id,
        )


class ListCronsTool(Tool):
    def __init__(self, scheduler: CronScheduler):
        self.scheduler = scheduler

    @property
    def name(self) -> str:
        return "list_crons"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "List scheduled cron jobs.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    def run(self) -> str:
        return self.scheduler.list_jobs()


class CancelCronTool(Tool):
    def __init__(self, scheduler: CronScheduler):
        self.scheduler = scheduler

    @property
    def name(self) -> str:
        return "cancel_cron"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Cancel a scheduled cron job.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                },
                "required": ["job_id"],
            },
        }

    def run(self, job_id: str) -> str:
        return self.scheduler.cancel(job_id)


class ListBackgroundTasksTool(Tool):
    def __init__(self, background_tasks: BackgroundTasks):
        self.background_tasks = background_tasks

    @property
    def name(self) -> str:
        return "list_background_tasks"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "List currently tracked background tool executions.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    def run(self) -> str:
        return self.background_tasks.list_tasks()


class GetBackgroundTaskTool(Tool):
    def __init__(self, background_tasks: BackgroundTasks):
        self.background_tasks = background_tasks

    @property
    def name(self) -> str:
        return "get_background_task"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Get status and result for one background task.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        }

    def run(self, task_id: str) -> str:
        return self.background_tasks.get_task(task_id)


class CancelBackgroundTaskTool(Tool):
    def __init__(self, background_tasks: BackgroundTasks):
        self.background_tasks = background_tasks

    @property
    def name(self) -> str:
        return "cancel_background_task"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": (
                "Request cancellation for a background task. "
                "This marks cancellation; running threads cannot be force-killed."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        }

    def run(self, task_id: str) -> str:
        return self.background_tasks.cancel_task(task_id)


class WaitBackgroundTaskTool(Tool):
    def __init__(self, background_tasks: BackgroundTasks):
        self.background_tasks = background_tasks

    @property
    def name(self) -> str:
        return "wait_background_task"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Wait briefly for a background task to finish and return its status.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                },
                "required": ["task_id"],
            },
        }

    def run(self, task_id: str, timeout_seconds: int = 30) -> str:
        return self.background_tasks.wait_task(task_id, timeout_seconds)


class SpawnTeammateTool(Tool):
    def __init__(self, teams: AgentTeams):
        self.teams = teams

    @property
    def name(self) -> str:
        return "spawn_teammate"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Spawn a teammate agent in a background thread.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["name", "role", "prompt"],
            },
        }

    def run(self, name: str, role: str, prompt: str) -> str:
        return self.teams.spawn_teammate(name, role, prompt)


class SendMessageTool(Tool):
    def __init__(self, teams: AgentTeams, from_agent: str = "lead"):
        self.teams = teams
        self.from_agent = from_agent

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Send a message to another agent mailbox.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to_agent": {"type": "string"},
                    "content": {"type": "string"},
                    "msg_type": {"type": "string"},
                },
                "required": ["to_agent", "content"],
            },
        }

    def run(self, to_agent: str, content: str, msg_type: str = "message") -> str:
        return self.teams.send_message(to_agent, content, self.from_agent, msg_type)


class CheckInboxTool(Tool):
    def __init__(self, teams: AgentTeams, agent: str = "lead"):
        self.teams = teams
        self.agent = agent

    @property
    def name(self) -> str:
        return "check_inbox"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Read and clear the current agent inbox.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string"},
                },
                "required": [],
            },
        }

    def run(self, agent: str = "") -> str:
        return self.teams.check_inbox(agent or self.agent)


class RequestShutdownTool(Tool):
    def __init__(self, teams: AgentTeams):
        self.teams = teams

    @property
    def name(self) -> str:
        return "request_shutdown"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Send a structured shutdown_request to a teammate.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "teammate": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["teammate"],
            },
        }

    def run(self, teammate: str, reason: str = "") -> str:
        return self.teams.request_shutdown(teammate, reason)


class RequestPlanTool(Tool):
    def __init__(self, teams: AgentTeams):
        self.teams = teams

    @property
    def name(self) -> str:
        return "request_plan"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Ask a teammate to submit a plan before acting.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "teammate": {"type": "string"},
                    "task": {"type": "string"},
                },
                "required": ["teammate", "task"],
            },
        }

    def run(self, teammate: str, task: str) -> str:
        return self.teams.request_plan(teammate, task)


class ReviewPlanTool(Tool):
    def __init__(self, teams: AgentTeams):
        self.teams = teams

    @property
    def name(self) -> str:
        return "review_plan"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Approve or reject a teammate plan_approval_request by request_id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "approve": {"type": "boolean"},
                    "feedback": {"type": "string"},
                },
                "required": ["request_id", "approve"],
            },
        }

    def run(self, request_id: str, approve: bool, feedback: str = "") -> str:
        return self.teams.review_plan(request_id, approve, feedback)


class SubmitPlanTool(Tool):
    def __init__(self, teams: AgentTeams, from_agent: str):
        self.teams = teams
        self.from_agent = from_agent

    @property
    def name(self) -> str:
        return "submit_plan"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Submit a plan to lead and wait for approval before high-risk work.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "plan": {"type": "string"},
                },
                "required": ["plan"],
            },
        }

    def run(self, plan: str) -> str:
        return self.teams.submit_plan(self.from_agent, plan)


class ProtocolStatusTool(Tool):
    def __init__(self, teams: AgentTeams):
        self.teams = teams

    @property
    def name(self) -> str:
        return "protocol_status"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Show pending and resolved team protocol requests.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    def run(self) -> str:
        return self.teams.protocol_status()


class CreateWorktreeTool(Tool):
    def __init__(self, manager: WorktreeManager):
        self.manager = manager

    @property
    def name(self) -> str:
        return "create_worktree"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Create an isolated git worktree and optional task binding.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "task_id": {"type": "string"},
                },
                "required": ["name"],
            },
        }

    def run(self, name: str, task_id: str = "") -> str:
        return self.manager.create_worktree(name, task_id)


class BindTaskToWorktreeTool(Tool):
    def __init__(self, manager: WorktreeManager):
        self.manager = manager

    @property
    def name(self) -> str:
        return "bind_task_to_worktree"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Bind an existing persistent task to an existing worktree.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "worktree_name": {"type": "string"},
                },
                "required": ["task_id", "worktree_name"],
            },
        }

    def run(self, task_id: str, worktree_name: str) -> str:
        return self.manager.bind_task_to_worktree(task_id, worktree_name)


class RemoveWorktreeTool(Tool):
    def __init__(self, manager: WorktreeManager):
        self.manager = manager

    @property
    def name(self) -> str:
        return "remove_worktree"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": (
                "Remove a git worktree. Refuses changed files unless "
                "discard_changes=true."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "discard_changes": {"type": "boolean"},
                },
                "required": ["name"],
            },
        }

    def run(self, name: str, discard_changes: bool = False) -> str:
        return self.manager.remove_worktree(name, discard_changes)


class KeepWorktreeTool(Tool):
    def __init__(self, manager: WorktreeManager):
        self.manager = manager

    @property
    def name(self) -> str:
        return "keep_worktree"

    @property
    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": "Keep a worktree and branch for manual review.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        }

    def run(self, name: str) -> str:
        return self.manager.keep_worktree(name)


class Tools:
    def __init__(
        self,
        workdir: Path,
        workdir_provider=None,
        task_runner=None,
        skill_loader=None,
        compact_enabled: bool = False,
        task_system: TaskSystem | None = None,
        task_tool_mode: str = "full",
        after_task_claim=None,
        after_task_complete=None,
        background_tasks: BackgroundTasks | None = None,
        cron_scheduler: CronScheduler | None = None,
        agent_teams: AgentTeams | None = None,
        agent_name: str = "lead",
        worktree_manager: WorktreeManager | None = None,
        personal_profile=None,
        personal_memory=None,
        personal_state=None,
        daily_planner=None,
    ):
        self.workdir = workdir.resolve()
        self.workdir_provider = workdir_provider
        self.registry = {}
        self.validator = ToolCallValidator()
        self.todo_tool = TodoWriteTool()

        self.register(BashTool(self.workdir, self.workdir_provider))
        self.register(ReadFileTool(self.workdir, self.workdir_provider))
        self.register(WriteFileTool(self.workdir, self.workdir_provider))
        self.register(EditFileTool(self.workdir, self.workdir_provider))
        self.register(GlobTool(self.workdir, self.workdir_provider))
        self.register(self.todo_tool)

        if personal_profile is not None and personal_memory is not None:
            for personal_tool in build_personal_tools(personal_profile, personal_memory):
                self.register(personal_tool)
        if personal_state is not None:
            for state_tool in build_personal_state_tools(personal_state):
                self.register(state_tool)
        if daily_planner is not None:
            for daily_tool in build_daily_tools(daily_planner):
                self.register(daily_tool)

        if skill_loader is not None:
            self.register(LoadSkillTool(skill_loader))
        if compact_enabled:
            self.register(CompactTool())
        if task_runner is not None:
            self.register(TaskTool(task_runner))
        if task_system is not None and task_tool_mode == "full":
            self.register(CreateTaskTool(task_system))
            self.register(ListTasksTool(task_system))
            self.register(GetTaskTool(task_system))
            self.register(ClaimTaskTool(task_system, after_task_claim))
            self.register(CompleteTaskTool(task_system, after_task_complete))
        if task_system is not None and task_tool_mode == "worker":
            self.register(ListTasksTool(task_system))
            self.register(GetTaskTool(task_system))
            self.register(ClaimTaskTool(task_system, after_task_claim))
            self.register(CompleteTaskTool(task_system, after_task_complete))
        if cron_scheduler is not None:
            self.register(ScheduleCronTool(cron_scheduler))
            self.register(ListCronsTool(cron_scheduler))
            self.register(CancelCronTool(cron_scheduler))
        if background_tasks is not None:
            self.register(ListBackgroundTasksTool(background_tasks))
            self.register(GetBackgroundTaskTool(background_tasks))
            self.register(CancelBackgroundTaskTool(background_tasks))
            self.register(WaitBackgroundTaskTool(background_tasks))
        if agent_teams is not None:
            if agent_name == "lead":
                self.register(SpawnTeammateTool(agent_teams))
                self.register(CheckInboxTool(agent_teams, agent_name))
                self.register(RequestShutdownTool(agent_teams))
                self.register(RequestPlanTool(agent_teams))
                self.register(ReviewPlanTool(agent_teams))
                self.register(ProtocolStatusTool(agent_teams))
            else:
                self.register(SubmitPlanTool(agent_teams, agent_name))
            self.register(SendMessageTool(agent_teams, agent_name))
        if worktree_manager is not None and agent_name == "lead":
            self.register(CreateWorktreeTool(worktree_manager))
            self.register(BindTaskToWorktreeTool(worktree_manager))
            self.register(RemoveWorktreeTool(worktree_manager))
            self.register(KeepWorktreeTool(worktree_manager))

    @property
    def current_todos(self) -> list:
        return self.todo_tool.current_todos

    @property
    def definitions(self) -> list:
        return [tool.definition for tool in self.registry.values()]

    def register(self, tool: Tool) -> None:
        self.registry[tool.name] = tool

    def execute(self, block) -> str:
        block_error = self.validator.validate_block(block)
        if block_error:
            return ToolResult.error(
                "InvalidToolUse",
                block_error,
                recoverable=True,
            ).to_tool_content()

        tool = self.registry.get(block.name)
        if tool is None:
            return ToolResult.error(
                "UnknownTool",
                f"Unknown tool: {block.name}",
                recoverable=True,
            ).to_tool_content()

        input_error = self.validator.validate_input(tool.definition, block.input)
        if input_error:
            return ToolResult.error(
                "InvalidToolInput",
                input_error,
                recoverable=True,
            ).to_tool_content()

        try:
            output = tool.run(**block.input)
        except Exception as exc:
            return ToolResult.error(
                type(exc).__name__,
                str(exc),
                recoverable=True,
            ).to_tool_content()

        if isinstance(output, str) and output.startswith("Error:"):
            return ToolResult.error(
                "ToolExecutionError",
                output,
                recoverable=True,
            ).to_tool_content()

        return ToolResult.success(str(output)).to_tool_content()
