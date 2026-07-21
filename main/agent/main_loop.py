#!/usr/bin/env python3

import copy
import os
import threading
import time
from pathlib import Path

from main.teams.agent_teams import AgentTeams
from main.agent.runtime import AgentRuntime
from main.tasks.background_tasks import BackgroundTasks
from main.storage.context_compact import ContextCompactor
from main.tasks.cron_scheduler import CronScheduler
from main.agent.error_handler import DirectErrorHandler, ErrorHandler
from main.agent.error_recovery import ErrorRecovery, RecoveryState
from main.tools.hooks import Hooks
from main.llm.http import client, ensure_configured, get_settings
from main.llm.gateway import LlmGateway
from main.agent.loop_guard import LoopGuard
from main.memory import (
    MemoryContextAssembler,
    MemoryConsolidator,
    MemoryMaintenanceService,
    MemoryMode,
    MemoryRuntimeConfig,
    MemoryWorkspaceSync,
    PersonalMemoryManager,
    PersonalMemoryRetriever,
    StructuredMemoryPipeline,
    LegacyMemoryMigration,
    LegacyMemoryAudit,
    ControlledLlmMemoryExtractor,
)
from main.conversation import ConversationMemoryRepository, ConversationMemoryService
from main.assistant import (
    DailyPlanner,
    Persona,
    PersonalStateManager,
    ProfileStore,
    ReminderDispatcher,
    RoutineDispatcher,
)
from main.personal import RoutineManager
from main.tools.permissions import Permissions
import main.agent.runtime_context as RuntimeContext
from main.prompt.skills import Skills
from main.llm.structured_output import ModelOutputValidator
from main.prompt.system_prompt import Prompt, SystemPrompt
from main.tasks.task_system import TaskSystem
from main.tools.tools import Tools
from main.teams.worktree_manager import WorktreeManager

ROOT_DIR = Path(__file__).resolve().parents[2]
CHANNEL_DIR = ROOT_DIR / "main" / "channel"
import sys
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main.channel import ChannelMessage, ChannelRegistry, ChannelRuntime, TrustLevel  # noqa: E402
from main.channel.local import MemoryChannel, StdoutChannel  # noqa: E402
from main.channel.types import ChannelKind  # noqa: E402


SETTINGS = ensure_configured()
MODEL = SETTINGS.model
llm_gateway = LlmGateway(client, MODEL, logger=print)
WORKDIR = Path(os.environ.get("ANIYA_AGENT_WORKDIR", ROOT_DIR)).resolve()
permissions = Permissions(WORKDIR)
hooks = Hooks()
hooks.register("PreToolUse", permissions.check)
rounds_without_todo = 0
skills = Skills(WORKDIR)
memory_config = MemoryRuntimeConfig.from_env()
# Legacy Markdown memory is deliberately never initialized by the normal runtime.
memory = None
personal_workspace = MemoryWorkspaceSync(WORKDIR)
profile_store = ProfileStore(WORKDIR, workspace_sync=personal_workspace)
personal_memory_manager = PersonalMemoryManager(WORKDIR, workspace_sync=personal_workspace)
personal_memory_retriever = PersonalMemoryRetriever(personal_memory_manager)
conversation_memory = ConversationMemoryService(ConversationMemoryRepository(WORKDIR))
memory_context = MemoryContextAssembler(conversation_memory, personal_memory_retriever)
personal_state = PersonalStateManager(WORKDIR)
conversation_memory.personal_state = personal_state
memory_pipeline = StructuredMemoryPipeline(
    personal_memory_manager, conversation_memory, profile_store, personal_state,
    ControlledLlmMemoryExtractor(client, MODEL),
)
daily_planner = DailyPlanner(WORKDIR, personal_state, profile_store)
memory_consolidator = MemoryConsolidator(conversation_memory, personal_memory_manager)
memory_maintenance = MemoryMaintenanceService(
    WORKDIR, conversation_memory, memory_consolidator, daily_planner, profile_store,
)
legacy_memory_migration = LegacyMemoryMigration(WORKDIR, personal_memory_manager)
legacy_memory_audit = LegacyMemoryAudit(WORKDIR)
routine_manager = RoutineManager(WORKDIR)
personal_workspace.sync_profile(profile_store.get())
compactor = ContextCompactor(WORKDIR, llm_gateway, MODEL)
error_handler: ErrorHandler = DirectErrorHandler()
error_recovery = ErrorRecovery(MODEL)
model_output_validator = ModelOutputValidator()
loop_guard = LoopGuard()
task_system = TaskSystem(WORKDIR)
worktree_manager = WorktreeManager(WORKDIR, task_system)
background_tasks = BackgroundTasks()
cron_scheduler = CronScheduler(WORKDIR)
runtime = None
channel_registry = ChannelRegistry()
channel_runtime = None
cron_delivery_started = False
channel_registry.register(StdoutChannel("cli", ChannelKind.CLI, TrustLevel.HIGH))
channel_registry.register(MemoryChannel("web", ChannelKind.WEB, TrustLevel.HIGH))
channel_registry.register(MemoryChannel("cron", ChannelKind.CRON, TrustLevel.MEDIUM))
reminder_dispatcher = ReminderDispatcher(WORKDIR, personal_state, profile_store, channel_registry)
routine_dispatcher = RoutineDispatcher(
    WORKDIR,
    routine_manager,
    daily_planner,
    profile_store,
    channel_registry,
)


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
    llm_gateway,
    MODEL,
    teammate_tools_factory,
    task_system=task_system,
    worktree_manager=worktree_manager,
)
prompt_builder: Prompt = SystemPrompt(
    WORKDIR,
    skills,
    None,
    persona=Persona(),
    profile=profile_store,
    personal_state=personal_state,
)
MAX_REACTIVE_RETRIES = 3


TODO_REMINDER = (
    "If the current task is multi-step and the todo list has not been updated recently, "
    "consider calling todo_write before continuing. Do not interrupt a final answer after "
    "tool results just to update todos."
)


def build_system(runtime_reminders: list[str] | None = None) -> str:
    context = prompt_builder.parent_context(tools.definitions)
    system = prompt_builder.get_system_prompt(context)
    if runtime_reminders:
        reminders = "\n".join(f"- {reminder}" for reminder in runtime_reminders)
        system = f"{system}\n\n<runtime_reminders>\n{reminders}\n</runtime_reminders>"
    return system


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


def latest_user_text(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def message_has_tool_result(message: dict | None) -> bool:
    if not message or message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


def todo_runtime_reminders(messages: list) -> list[str]:
    if rounds_without_todo < 3:
        return []
    if message_has_tool_result(messages[-1] if messages else None):
        return []
    return [TODO_REMINDER]


def notification_blocks_to_reminders(notifications: list[dict]) -> list[str]:
    reminders = []
    for block in notifications:
        text = block.get("text") if isinstance(block, dict) else str(block)
        if text:
            reminders.append(str(text))
    return reminders


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


def cron_delivery_loop() -> None:
    while True:
        time.sleep(1)
        for job in cron_scheduler.consume_queue():
            handle_channel_message(
                ChannelMessage(
                    channel_id=job.target_channel,
                    user_id=job.user_id,
                    conversation_id=job.conversation_id,
                    text=f"[Scheduled cron {job.id}]\n{job.prompt}",
                    kind=ChannelKind.CRON,
                    trust_level=TrustLevel.MEDIUM,
                    metadata={
                        "cron_id": job.id,
                        "cron": job.cron,
                        "recurring": job.recurring,
                    },
                )
            )


def start_cron_delivery_loop() -> None:
    global cron_delivery_started
    if cron_delivery_started:
        return
    cron_delivery_started = True
    threading.Thread(target=cron_delivery_loop, daemon=True, name="cron-channel-delivery").start()


def run_tool_turn(
    messages: list,
    toolset: Tools,
    system: str,
    label: str,
    request_messages: list | None = None,
    active_compactor: ContextCompactor | None = None,
    recovery_state: RecoveryState | None = None,
) -> tuple[bool, set[str]]:
    RuntimeContext.ensure_deadline()
    recovery_state = recovery_state or error_recovery.new_state()
    current_request_messages = request_messages or messages

    while True:
        RuntimeContext.ensure_deadline()
        RuntimeContext.event(
            "llm.request.started",
            {
                "label": label,
                "messages": len(current_request_messages),
                "tools": len(toolset.definitions),
                "model": recovery_state.current_model,
                "max_tokens": recovery_state.max_tokens,
            },
        )
        try:
            response = error_recovery.call_model(
                llm_gateway,
                recovery_state,
                system=system,
                messages=current_request_messages,
                tools=toolset.definitions,
            )
        except Exception as exc:
            RuntimeContext.event(
                "llm.request.failed",
                {
                    "label": label,
                    "model": recovery_state.current_model,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            raise
        RuntimeContext.event(
            "llm.request.completed",
            {
                "label": label,
                "stop_reason": getattr(response, "stop_reason", ""),
                "content_blocks": len(getattr(response, "content", []) or []),
                "model": recovery_state.current_model,
                "max_tokens": recovery_state.max_tokens,
            },
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
            RuntimeContext.ensure_deadline()
            used_tools.add(block.name)
            print(f"\n{label}> {block.name}")
            RuntimeContext.event(
                "tool.call.started",
                {
                    "label": label,
                    "tool": RuntimeContext.tool_payload(block),
                },
            )

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
                RuntimeContext.event(
                    "tool.call.completed",
                    {
                        "label": label,
                        "tool": RuntimeContext.tool_payload(block),
                        "result": RuntimeContext.preview("[Compacted. Conversation history has been summarized.]"),
                    },
                )
                return True, used_tools

            blocked = hooks.trigger("PreToolUse", block)
            if blocked:
                output = blocked
                RuntimeContext.event(
                    "tool.call.blocked",
                    {
                        "label": label,
                        "tool": RuntimeContext.tool_payload(block),
                        "reason": str(blocked),
                    },
                )
            elif background_tasks.should_run_background(block):
                bg_id = background_tasks.start(block, toolset.execute)
                output = (
                    f"[Background task {bg_id} started] "
                    "Result will be delivered as a task_notification when complete."
                )
            else:
                output = toolset.execute(block)
            RuntimeContext.ensure_deadline()
            hooks.trigger("PostToolUse", block, output)
            print(str(output)[:200])
            RuntimeContext.event(
                "tool.call.completed",
                {
                    "label": label,
                    "tool": RuntimeContext.tool_payload(block),
                    "result": RuntimeContext.preview(output),
                },
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )

    messages.append({"role": "user", "content": results})
    return True, used_tools


def run_tool_loop(messages: list, toolset: Tools, system: str, label: str, max_turns: int = 30) -> None:
    recovery_state = error_recovery.new_state()
    for _ in range(max_turns):
        RuntimeContext.ensure_deadline()
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
    personal_profile=profile_store,
    personal_memory=personal_memory_manager,
    personal_state=personal_state,
    daily_planner=daily_planner,
    routine_manager=routine_manager,
    routine_dispatcher=routine_dispatcher,
    conversation_memory=conversation_memory,
    legacy_memory_migration=legacy_memory_migration if memory_config.allow_legacy_migration else None,
    capability_profile="personal_assistant",
)


def agent_loop(messages: list) -> None:
    global rounds_without_todo

    reactive_retries = 0
    recovery_state = error_recovery.new_state()
    loop_state = loop_guard.new_state()
    if memory_config.audit_legacy:
        audit = legacy_memory_audit.report()
        RuntimeContext.event("memory.legacy_audit", {"file_count": audit["file_count"], "estimated_prompt_tokens": audit["estimated_prompt_tokens"]})
    memories_content = memory_context.assemble(latest_user_text(messages))
    memory_turn = len(messages) - 1 if messages and isinstance(messages[-1].get("content"), str) else None

    while True:
        RuntimeContext.ensure_deadline()
        if loop_guard.begin_turn(loop_state) == "stop":
            RuntimeContext.event("loop.guard.stopped", {"reason": "max_turns"})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Loop guard stopped the agent after too many iterations without progress. "
                        "Summarize the blocker and ask the user for a new direction."
                    ),
                }
            )
            return

        RuntimeContext.begin_turn(messages)
        runtime_system_notifications = collect_system_notifications()
        pre_compact = copy.deepcopy(messages)
        messages[:] = compactor.preprocess(messages)

        if compactor.should_auto_compact(messages):
            print("[auto compact]")
            messages[:] = compactor.compact_history(messages)

        runtime_reminders = notification_blocks_to_reminders(runtime_system_notifications)
        runtime_reminders.extend(todo_runtime_reminders(messages))
        if runtime_reminders:
            rounds_without_todo = 0

        request_messages = build_request_messages(messages, memories_content, memory_turn)
        try:
            needs_more_tools, used_tools = run_tool_turn(
                messages,
                tools,
                build_system(runtime_reminders),
                "parent",
                request_messages=request_messages,
                active_compactor=compactor,
                recovery_state=recovery_state,
            )
            RuntimeContext.end_turn(messages, needs_more_tools, used_tools)
            reactive_retries = 0
        except Exception as exc:
            RuntimeContext.event(
                "loop.turn.failed",
                {"error_type": type(exc).__name__, "message": str(exc)},
            )
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
            loop_guard.reset(loop_state)
            return

        loop_action = loop_guard.observe_turn(loop_state, messages, needs_more_tools)
        if loop_action == "nudge":
            RuntimeContext.event("loop.guard.nudged", {"reason": "repeated_turn_signature"})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "No progress has been made across repeated turns. "
                        "Do not repeat the same tool call. Re-evaluate the task, "
                        "try a different approach, or ask the user for clarification."
                    ),
                }
            )
            continue

        if loop_action == "stop":
            RuntimeContext.event("loop.guard.stopped", {"reason": "repeated_turn_signature"})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "The agent is looping without visible progress. "
                        "Stop now, summarize the blocker, and ask the user for help."
                    ),
                }
            )
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


def get_runtime() -> AgentRuntime:
    global runtime
    if runtime is None:
        runtime = AgentRuntime(
            WORKDIR, agent_loop, extract_text, conversation_memory=conversation_memory,
            profile_store=profile_store, memory_pipeline=memory_pipeline,
            memory_maintenance=memory_maintenance,
            memory_source_provider=lambda: memory_context.last_sources,
        )
    return runtime


def get_channel_runtime() -> ChannelRuntime:
    global channel_runtime
    if channel_runtime is None:
        channel_runtime = ChannelRuntime(get_runtime(), channel_registry)
    return channel_runtime


def handle_channel_message(message: ChannelMessage, deliver: bool = True, event_callback=None):
    hooks.trigger("UserPromptSubmit", message.text)
    return get_channel_runtime().handle_message(message, deliver=deliver, event_callback=event_callback)


def start_background_services() -> None:
    """Explicit scheduler-process ownership; importing the composition module has no side effects."""
    cron_scheduler.start()
    start_cron_delivery_loop()
    reminder_dispatcher.start()
    routine_dispatcher.start()
    memory_maintenance.start()


if __name__ == "__main__":
    start_background_services()
    print("AniyaAgent: Agent Loop")
    print(f"Model: {get_settings().model}")
    print("Type a task and press Enter. Type q to quit.\n")

    while True:
        try:
            query = input("S01 >> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if query.strip().lower() in {"q", "exit", "quit", ""}:
            break

        handle_channel_message(
            ChannelMessage(
                channel_id="cli",
                user_id="local",
                conversation_id="cli",
                text=query,
                kind=ChannelKind.CLI,
                trust_level=TrustLevel.HIGH,
            )
        )
        print()
