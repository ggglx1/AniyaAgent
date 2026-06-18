#!/usr/bin/env python3

import copy
from pathlib import Path

from AgentTeams import AgentTeams
from BackgroundTasks import BackgroundTasks
from ContextCompact import ContextCompactor
from CronScheduler import CronScheduler
from ErrorHandler import DirectErrorHandler, ErrorHandler
from ErrorRecovery import ErrorRecovery, RecoveryState
from Hooks import Hooks
from llm_http import client, ensure_configured, get_settings
from Memory import Memory
from Permissions import Permissions
from Skills import Skills
from StructuredOutput import ModelOutputValidator
from SystemPrompt import Prompt, SystemPrompt
from TaskSystem import TaskSystem
from Tools import Tools
from WorktreeManager import WorktreeManager


SETTINGS = ensure_configured()
MODEL = SETTINGS.model
WORKDIR = Path(__file__).resolve().parent.parent
permissions = Permissions(WORKDIR)
hooks = Hooks()
hooks.register("PreToolUse", permissions.check)
rounds_without_todo = 0
skills = Skills(WORKDIR)
memory = Memory(WORKDIR, client, MODEL)
compactor = ContextCompactor(WORKDIR, client, MODEL)
error_handler: ErrorHandler = DirectErrorHandler()
error_recovery = ErrorRecovery(MODEL)
model_output_validator = ModelOutputValidator()
task_system = TaskSystem(WORKDIR)
worktree_manager = WorktreeManager(WORKDIR, task_system)
background_tasks = BackgroundTasks()
cron_scheduler = CronScheduler(WORKDIR)


def teammate_tools_factory(name: str) -> Tools:
    return Tools(
        WORKDIR,
        workdir_provider=lambda: agent_teams.teammate_workdir(name),
        task_system=task_system,
        task_tool_mode="worker",
        after_task_claim=lambda task_id, owner: agent_teams.activate_task_worktree(
            name,
            task_id,
        ),
        after_task_complete=lambda task_id: agent_teams.reset_teammate_workdir(name),
        agent_teams=agent_teams,
        agent_name=name,
    )


agent_teams = AgentTeams(
    WORKDIR,
    client,
    MODEL,
    teammate_tools_factory,
    task_system=task_system,
    worktree_manager=worktree_manager,
)
cron_scheduler.start()
prompt_builder: Prompt = SystemPrompt(WORKDIR, skills, memory)
MAX_REACTIVE_RETRIES = 3


def build_system() -> str:
    context = prompt_builder.parent_context(tools.definitions)
    return prompt_builder.get_system_prompt(context)


def build_subagent_system(sub_tools: Tools) -> str:
    context = prompt_builder.subagent_context(sub_tools.definitions)
    return prompt_builder.get_system_prompt(context)


def extract_text(message_content) -> str:
    if not isinstance(message_content, list):
        return str(message_content)

    parts = []
    for block in message_content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts)


def build_request_messages(messages: list, memories_content: str, memory_turn: int | None) -> list:
    if not memories_content or memory_turn is None or memory_turn >= len(messages):
        return messages

    target = messages[memory_turn]
    if not isinstance(target.get("content"), str):
        return messages

    request_messages = messages.copy()
    request_messages[memory_turn] = {
        **target,
        "content": f"{memories_content}\n\n{target['content']}",
    }
    return request_messages


def collect_system_notifications() -> list[dict]:
    notifications = []
    for text in background_tasks.collect_notifications():
        notifications.append({"type": "text", "text": text})
    for text in agent_teams.collect_lead_messages():
        notifications.append({"type": "text", "text": text})
    return notifications


def inject_cron_jobs(messages: list) -> None:
    for job in cron_scheduler.consume_queue():
        messages.append(
            {
                "role": "user",
                "content": f"[Scheduled cron {job.id}]\n{job.prompt}",
            }
        )


def inject_system_notifications(messages: list) -> None:
    notifications = collect_system_notifications()
    if notifications:
        messages.append({"role": "user", "content": notifications})


def run_tool_turn(
    messages: list,
    toolset: Tools,
    system: str,
    label: str,
    request_messages: list | None = None,
    active_compactor: ContextCompactor | None = None,
    recovery_state: RecoveryState | None = None,
) -> tuple[bool, set[str]]:
    recovery_state = recovery_state or error_recovery.new_state()
    current_request_messages = request_messages or messages

    while True:
        response = error_recovery.call_model(
            client,
            recovery_state,
            system=system,
            messages=current_request_messages,
            tools=toolset.definitions,
        )

        recovery_action = error_recovery.recover_max_tokens(
            response,
            messages,
            recovery_state,
        )
        if recovery_action == "retry_same_request":
            continue
        if recovery_action == "retry_with_messages":
            current_request_messages = messages
            continue
        if recovery_action == "stop":
            return False, set()
        break

    output_error = model_output_validator.validate_response_content(response.content)
    if output_error:
        error_handler.append_message(messages, "Model output format error", output_error)
        return False, set()

    messages.append({"role": "assistant", "content": response.content})

    if response.stop_reason != "tool_use":
        return False, set()

    used_tools = set()
    results = []
    for block in response.content:
        if block.type == "tool_use":
            used_tools.add(block.name)
            print(f"\n{label}> {block.name}")

            if block.name == "compact" and active_compactor is not None:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "[Compacted. Conversation history has been summarized.]",
                    }
                )
                messages.append({"role": "user", "content": results})
                messages[:] = active_compactor.compact_history(messages)
                return True, used_tools

            blocked = hooks.trigger("PreToolUse", block)
            if blocked:
                output = blocked
            elif background_tasks.should_run_background(block):
                bg_id = background_tasks.start(block, toolset.execute)
                output = (
                    f"[Background task {bg_id} started] "
                    "Result will be delivered as a task_notification when complete."
                )
            else:
                output = toolset.execute(block)
            hooks.trigger("PostToolUse", block, output)
            print(str(output)[:200])
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )

    user_content = collect_system_notifications()
    user_content.extend(results)
    messages.append({"role": "user", "content": user_content})
    return True, used_tools


def run_tool_loop(messages: list, toolset: Tools, system: str, label: str, max_turns: int = 30) -> None:
    recovery_state = error_recovery.new_state()
    for _ in range(max_turns):
        try:
            needs_more_tools, _ = run_tool_turn(
                messages,
                toolset,
                system,
                label,
                recovery_state=recovery_state,
            )
        except Exception as exc:
            if not error_handler.handle(messages, exc):
                raise
            return

        if not needs_more_tools:
            return


def spawn_subagent(description: str) -> str:
    print("\n[Subagent spawned]")
    sub_tools = Tools(WORKDIR)
    sub_messages = [{"role": "user", "content": description}]
    run_tool_loop(sub_messages, sub_tools, build_subagent_system(sub_tools), "sub", max_turns=30)

    summary = ""
    for message in reversed(sub_messages):
        if message["role"] == "assistant":
            summary = extract_text(message["content"])
            if summary:
                break

    print("[Subagent done]")
    return summary or "Subagent stopped without a final summary."


tools = Tools(
    WORKDIR,
    task_runner=spawn_subagent,
    skill_loader=skills.load_skill,
    compact_enabled=True,
    task_system=task_system,
    background_tasks=background_tasks,
    cron_scheduler=cron_scheduler,
    agent_teams=agent_teams,
    worktree_manager=worktree_manager,
)


def agent_loop(messages: list) -> None:
    global rounds_without_todo

    reactive_retries = 0
    recovery_state = error_recovery.new_state()
    memories_content = memory.load_memories(messages)
    memory_turn = len(messages) - 1 if messages and isinstance(messages[-1].get("content"), str) else None

    while True:
        inject_cron_jobs(messages)
        inject_system_notifications(messages)
        pre_compact = copy.deepcopy(messages)
        messages[:] = compactor.preprocess(messages)

        if compactor.should_auto_compact(messages):
            print("[auto compact]")
            messages[:] = compactor.compact_history(messages)

        if rounds_without_todo >= 3:
            messages.append({
                "role": "user",
                "content": (
                    "Reminder: You have not updated the todo list recently. "
                    "If this is a multi-step task, call todo_write to update "
                    "the plan and current progress before continuing."
                ),
            })
            rounds_without_todo = 0

        request_messages = build_request_messages(messages, memories_content, memory_turn)
        try:
            needs_more_tools, used_tools = run_tool_turn(
                messages,
                tools,
                build_system(),
                "parent",
                request_messages=request_messages,
                active_compactor=compactor,
                recovery_state=recovery_state,
            )
            reactive_retries = 0
        except Exception as exc:
            if compactor.is_prompt_too_long(exc) and reactive_retries < MAX_REACTIVE_RETRIES:
                level = reactive_retries + 1
                print(f"[reactive compact level {level}]")
                messages[:] = compactor.reactive_compact(messages, level=level)
                reactive_retries += 1
                continue
            if error_handler.handle(messages, exc):
                return
            raise

        if not needs_more_tools:
            hooks.trigger("Stop", messages)
            memory.extract_memories(pre_compact)
            memory.consolidate_memories()
            return

        if "todo_write" in used_tools:
            rounds_without_todo = 0
        else:
            rounds_without_todo += 1


def print_final_text(message_content) -> None:
    if not isinstance(message_content, list):
        return

    for block in message_content:
        if block.type == "text":
            print(block.text)


if __name__ == "__main__":
    print("HappyClaude: Agent Loop")
    print(f"Model: {get_settings().model}")
    print("Type a task and press Enter. Type q to quit.\n")

    history = []
    while True:
        try:
            query = input("S01 >> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if query.strip().lower() in {"q", "exit", "quit", ""}:
            break

        hooks.trigger("UserPromptSubmit", query)
        history.append({"role": "user", "content": query})
        agent_loop(history)
        print_final_text(history[-1]["content"])
        print()
