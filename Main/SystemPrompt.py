import json
from abc import ABC, abstractmethod
from pathlib import Path


class Prompt(ABC):
    @abstractmethod
    def parent_context(self, tool_definitions: list[dict]) -> dict:
        pass

    @abstractmethod
    def subagent_context(self, tool_definitions: list[dict]) -> dict:
        pass

    @abstractmethod
    def get_system_prompt(self, context: dict) -> str:
        pass


class SystemPrompt(Prompt):
    def __init__(self, workdir: Path, skills, memory):
        self.workdir = workdir.resolve()
        self.skills = skills
        self.memory = memory
        self.last_context_key = None
        self.last_prompt = None

    def parent_context(self, tool_definitions: list[dict]) -> dict:
        return self.update_context(tool_definitions, agent_role="parent")

    def subagent_context(self, tool_definitions: list[dict]) -> dict:
        return self.update_context(tool_definitions, agent_role="subagent")

    def update_context(self, tool_definitions: list[dict], agent_role: str = "parent") -> dict:
        if agent_role not in {"parent", "subagent"}:
            raise ValueError(f"Unknown agent role: {agent_role}")

        tool_names = [definition["name"] for definition in tool_definitions]
        skill_catalog = ""
        memory_index = ""

        if agent_role == "parent":
            loaded_skills = self.skills.catalog_text()
            skill_catalog = "" if loaded_skills == "(no skills found)" else loaded_skills
            memory_index = self.memory.read_memory_index()

        return {
            "agent_role": agent_role,
            "workspace": str(self.workdir),
            "enabled_tools": tool_names,
            "skill_catalog": skill_catalog,
            "memory_index": memory_index,
        }

    def get_system_prompt(self, context: dict) -> str:
        context_key = json.dumps(
            context,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        if context_key == self.last_context_key and self.last_prompt:
            return self.last_prompt

        self.last_context_key = context_key
        self.last_prompt = self.assemble_system_prompt(context)
        return self.last_prompt

    def assemble_system_prompt(self, context: dict) -> str:
        enabled_tools = context.get("enabled_tools", [])
        sections = [
            self.identity_section(context),
            self.workspace_section(context),
            self.tools_section(enabled_tools),
        ]

        if "todo_write" in enabled_tools:
            sections.append(self.todo_section())
        if context.get("agent_role") == "parent" and "task" in enabled_tools:
            sections.append(self.subagent_section())
        if context.get("agent_role") == "parent" and "create_task" in enabled_tools:
            sections.append(self.task_system_section())
        if context.get("agent_role") == "parent" and "schedule_cron" in enabled_tools:
            sections.append(self.cron_section())
        if context.get("agent_role") == "parent" and "spawn_teammate" in enabled_tools:
            sections.append(self.team_section())
        if context.get("agent_role") == "parent" and "create_worktree" in enabled_tools:
            sections.append(self.worktree_section())
        if "bash" in enabled_tools:
            sections.append(self.background_section())
        if context.get("agent_role") == "parent" and "list_background_tasks" in enabled_tools:
            sections.append(self.background_tasks_section())
        if context.get("agent_role") == "parent" and "load_skill" in enabled_tools and context.get("skill_catalog"):
            sections.append(self.skills_section(context["skill_catalog"]))
        if context.get("agent_role") == "parent" and "compact" in enabled_tools:
            sections.append(self.compact_section())
        if context.get("agent_role") == "parent" and context.get("memory_index"):
            sections.append(self.memory_section(context["memory_index"]))

        return "\n\n".join(sections)

    def identity_section(self, context: dict) -> str:
        if context.get("agent_role") == "subagent":
            return (
                "You are a HappyClaude subagent. "
                "You receive a fresh conversation containing only the delegated task. "
                "Complete only that task, use tools when needed, then return a concise final summary. "
                "Do not delegate further."
            )

        return (
            "You are HappyClaude, a local coding agent. "
            "Use tools to solve tasks. Act, don't explain unless the user asks for explanation."
        )

    def workspace_section(self, context: dict) -> str:
        return f"Working directory: {context['workspace']}"

    def tools_section(self, enabled_tools: list[str]) -> str:
        return (
            "Available tools:\n"
            f"{', '.join(enabled_tools)}\n"
            "Only call tools from this list. Do not invent tool names."
        )

    def todo_section(self) -> str:
        return (
            "Task tracking:\n"
            "For multi-step tasks, use todo_write to keep the current plan and progress visible."
        )

    def subagent_section(self) -> str:
        return (
            "Subagents:\n"
            "For complex investigation or isolated subtasks, use the task tool. "
            "The subagent returns only a concise final conclusion."
        )

    def task_system_section(self) -> str:
        return (
            "Persistent task system:\n"
            "Use create_task, list_tasks, get_task, claim_task, and complete_task for durable work items. "
            "Tasks are saved under .tasks/ and survive across conversations. "
            "Use blockedBy to represent dependencies; claim_task refuses work whose dependencies are not completed. "
            "A task may also have a worktree field when it is bound to an isolated git worktree. "
            "Use todo_write for the current short-term execution checklist, not for durable task storage."
        )

    def background_section(self) -> str:
        return (
            "Background execution:\n"
            "For slow bash commands, set run_in_background=true. "
            "The tool_result returns a background task id immediately; completion arrives later as a task_notification."
        )

    def background_tasks_section(self) -> str:
        return (
            "Background task control:\n"
            "Use list_background_tasks to inspect running jobs. "
            "Use get_background_task(task_id) to inspect one job. "
            "Use wait_background_task(task_id, timeout_seconds) when a later step depends on a background result. "
            "Use cancel_background_task(task_id) to request cancellation."
        )

    def cron_section(self) -> str:
        return (
            "Cron scheduler:\n"
            "Use schedule_cron(cron, prompt, recurring, durable) for time-based prompts. "
            "Use five-field cron expressions like '*/5 * * * *'. "
            "Use list_crons and cancel_cron to inspect or remove schedules."
        )

    def team_section(self) -> str:
        return (
            "Agent teams:\n"
            "Use spawn_teammate(name, role, prompt) for parallel teammate work. "
            "Use send_message to communicate through file mailboxes and check_inbox to read messages. "
            "Use request_shutdown for graceful teammate shutdown. "
            "Use request_plan to ask a teammate for a plan, then review_plan(request_id, approve, feedback) "
            "to approve or reject the submitted plan. "
            "Teammates can idle, check the task board, auto-claim available tasks, and report results."
        )

    def worktree_section(self) -> str:
        return (
            "Worktree isolation:\n"
            "Use create_worktree(name, task_id) to create .worktrees/name on branch wt/name and optionally bind it to a task. "
            "Use bind_task_to_worktree(task_id, worktree_name) when the worktree already exists. "
            "When a teammate claims a task with a worktree binding, its file and bash tools run in that worktree. "
            "Use keep_worktree(name) for review. "
            "Use remove_worktree(name, discard_changes=false) only after checking whether the worktree should be removed."
        )

    def skills_section(self, skill_catalog: str) -> str:
        return (
            "Skills available:\n"
            f"{skill_catalog}\n"
            "Use load_skill(name) to load full skill instructions only when needed."
        )

    def compact_section(self) -> str:
        return (
            "Context compaction:\n"
            "Use compact when the conversation is becoming too large or hard to follow."
        )

    def memory_section(self, memory_index: str) -> str:
        return (
            "Memories available:\n"
            f"{memory_index}\n"
            "Relevant memory contents may be injected into the current user turn. "
            "Respect durable user preferences and project facts from memory."
        )
