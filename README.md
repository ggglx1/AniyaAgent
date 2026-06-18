# HappyClaude

HappyClaude is a local Agent CLI project inspired by Claude Code. It is built with Python 3.11 and focuses on the core engineering mechanisms behind an AI coding agent: ReAct loops, tool calling, local tool execution, permission checks, context management, task systems, multi-agent collaboration, scheduled jobs, and Git Worktree isolation.

HappyClaude provides a practical agent runtime for local execution, task orchestration, and multi-agent collaboration.

## Features

- ReAct-style agent loop with model `tool_use`, local tool execution, tool result feedback, and multi-turn reasoning.
- Unified tool interface and registry for file operations, shell commands, task management, background jobs, cron jobs, team communication, and worktree management.
- Hook and permission system for pre-tool checks, including path guard and risky command blocking.
- Context engineering with system prompt assembly, memory injection, skills loading, context compression, and reactive compaction.
- Persistent project task board based on `.tasks/*.json`, supporting task creation, claiming, completion, dependency checks, and worktree binding.
- Background task runtime for slow shell commands, including status query, waiting, cancellation request, and completion notifications.
- Cron scheduler for time-based prompt injection, with durable jobs saved to `.scheduled_tasks.json`.
- Agent team runtime with teammate agents, mailbox-based communication, structured protocols, plan approval, shutdown handling, and autonomous task claiming.
- Git Worktree isolation for task-level working directories and branches.
- Optional LangGraph entrypoint that maps the main agent loop into a `StateGraph`.

## Architecture

```text
User
  |
  v
MainLoop / MainLoopLangGraph
  |
  |-- SystemPrompt       prompt assembly
  |-- Tools              tool interface and registry
  |-- Hooks              lifecycle hooks
  |-- Permissions        path and command guard
  |-- ContextCompact     context compression
  |-- Memory             long-term memory
  |-- Skills             skill catalog and lazy loading
  |-- TaskSystem         persistent task board
  |-- BackgroundTasks    async tool execution
  |-- CronScheduler      time-based job queue
  |-- AgentTeams         multi-agent collaboration
  |-- WorktreeManager    git worktree isolation
  |
  v
LLM API
```

## Project Structure

```text
HappyClaude/
  Main/
    MainLoop.py              default CLI entrypoint
    MainLoopLangGraph.py     LangGraph-based entrypoint
    Tools.py                 tool interface and registry
    ToolResult.py            structured tool result format
    Permissions.py           permission checks
    Hooks.py                 hook system
    ContextCompact.py        context compression
    Memory.py                memory extraction and injection
    Skills.py                skill discovery and loading
    SystemPrompt.py          system prompt assembly
    TaskSystem.py            persistent task board
    BackgroundTasks.py       background tool execution
    CronScheduler.py         cron scheduler
    AgentTeams.py            multi-agent team runtime
    WorktreeManager.py       git worktree management
    ErrorHandler.py          user-facing error handling
    ErrorRecovery.py         model/API retry and recovery
    StructuredOutput.py      model output validation and repair
    llm_http/                Anthropic-compatible HTTP client
    requirements.txt
  skills/                    local skill files
  Test/                      local experiments
```

Runtime-generated directories:

```text
.tasks/            persistent task JSON files
.memory/           memory index and memory files
.mailboxes/        teammate inbox files
.task_outputs/     saved large tool outputs
.worktrees/        git worktree directories
```

## Requirements

- Python 3.11
- Git
- Conda or virtualenv
- Anthropic API key or an Anthropic-compatible API endpoint

## Security Before Publishing

Keep local credentials in `Main/.env`, which is ignored by Git. Only commit `Main/.env.example`, which contains placeholders.

If a real API key was ever committed, rotate or revoke it before making the repository public. Replacing the current file is not enough, because Git history can still contain older values.

## Quick Start

1. Create and activate a Python 3.11 environment.

```powershell
conda create -n Claude python=3.11
conda activate Claude
```

2. Install dependencies.

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\HappyClaude\Main
pip install -r requirements.txt
```

3. Create a `.env` file.

```powershell
Copy-Item .env.example .env
```

Edit `.env` and fill in your own values:

```env
ANTHROPIC_API_KEY=your_api_key_here
MODEL_ID=your_model_id_here
ANTHROPIC_BASE_URL=https://api.anthropic.com
```

If you use the official Anthropic API, `ANTHROPIC_BASE_URL` can be omitted.

4. Run the default agent loop.

```powershell
python MainLoop.py
```

5. Run the LangGraph version.

```powershell
python MainLoopLangGraph.py
```

## Core Concepts

### Agent Loop

`MainLoop.py` implements the default ReAct loop:

```text
user input -> model call -> tool_use -> local tool execution -> tool_result -> next model call
```

The loop also injects memory, scheduled jobs, teammate messages, background task notifications, and compacted context.

### Tool System

`Tools.py` defines a unified `Tool` interface. Each tool provides:

```text
name
definition
run(...)
```

The model only sees registered tool definitions. When it emits `tool_use`, the local runtime validates input, executes the tool, and returns a structured result.

### Task System

`TaskSystem.py` stores project-level tasks under `.tasks/`. A task contains:

```json
{
  "id": "task_xxx",
  "subject": "Task title",
  "description": "Task details",
  "status": "pending",
  "owner": null,
  "blockedBy": [],
  "worktree": null
}
```

`blockedBy` controls dependencies. A task can only be claimed when all dependency tasks are completed.

### Background Tasks

`BackgroundTasks.py` sends slow shell commands to daemon threads. The agent receives a background task id immediately and later receives a `<task_notification>` when the job finishes.

Supported lifecycle tools:

```text
list_background_tasks
get_background_task
cancel_background_task
wait_background_task
```

### Cron Scheduler

`CronScheduler.py` supports five-field cron expressions:

```text
minute hour day month weekday
```

Examples:

```text
*/5 * * * *      every 5 minutes
0 9 * * *        every day at 09:00
0 9 * * 1-5      weekdays at 09:00
```

Scheduled jobs are injected back into the conversation as user messages when triggered.

### Agent Teams

`AgentTeams.py` implements a Lead plus teammate model. Teammates run in separate daemon threads, keep their own context, and communicate through `.mailboxes/*.jsonl`.

The team runtime supports:

```text
spawn_teammate
send_message
check_inbox
request_shutdown
request_plan
review_plan
submit_plan
```

### Worktree Isolation

`WorktreeManager.py` creates task-level Git worktrees under `.worktrees/`. A task can be bound to a worktree so a teammate works in an isolated directory and branch.

This helps reduce file conflicts when multiple agents work in parallel.

## Example Prompts

```text
Create a task to inspect the Tools registry and another task to add tests. Make the test task depend on the inspection task.
```

```text
Run pytest in the background, then inspect the project structure while waiting.
```

```text
Schedule a recurring job every 5 minutes to check the git status.
```

```text
Spawn alice as a backend teammate and ask her to inspect TaskSystem.py.
```

```text
Create a worktree for a refactor task and bind the task to that worktree.
```

## Implementation Roadmap

The project is organized around the following implementation stages:

```text
S01 Agent Loop
S02 Tool Use
S03 Permission
S04 Hooks
S05 Todo
S06 Subagent
S07 Skills
S08 Context Compact
S09 Memory
S10 System Prompt
S11 Error Recovery
S12 Task System
S13 Background Tasks
S14 Cron Scheduler
S15 Agent Teams
S16 Team Protocols
S17 Autonomous Agents
S18 Worktree Isolation
S19 MCP Plugin
S20 Comprehensive Agent
```

HappyClaude currently implements most of the core runtime mechanisms through S18, plus an experimental LangGraph entrypoint.

## Notes

- Security and process isolation are intentionally simplified.
- Background task cancellation is cooperative. Python threads cannot be force-killed safely, so cancellation is recorded and applied when the underlying tool returns.
- Durable cron jobs are persisted, but the scheduler only runs while the Agent process is running.
- Before pushing to a public repository, run a secret scan and make sure `.env` and any real API keys are not committed.

## License

No license has been specified yet.
