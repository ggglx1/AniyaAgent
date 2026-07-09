# AniyaAgent Product Plan

## 1. Product Positioning

AniyaAgent should evolve from a personal Agent demo into a CowAgent-inspired personal, multi-channel, general-purpose Agent platform.

It should be positioned as a personal assistant. The product center is personal life, personal tasks, daily workflows, long-term memory, and multi-entry access.

Core positioning:

> A personal Agent reachable from Web, mobile, messaging platforms, scheduled jobs, and future channels. It remembers long-term user context, manages tasks, reminders, knowledge, and daily workflows, and safely uses local capabilities to get real work done.

Based on CowAgent, AniyaAgent should move toward:

- Multi-channel entry points instead of a single Web/CLI interface.
- One shared Agent brain instead of separate logic per entry point.
- Memory, tasks, skills, scheduling, and the control console as one personal workspace.
- Local tool capabilities serve personal workflows rather than becoming the product focus.

## 2. Current State

AniyaAgent already has a strong Agent core:

- Local Agent loop.
- Tool calling.
- Local execution for files, shell, tasks, schedules, and background jobs.
- Memory.
- Skills.
- Permission approval.
- Mobile Web access.
- Early multi-agent and subtask support.
- Tests and benchmarks.

From a CowAgent perspective, AniyaAgent is currently closer to an "Agent runtime plus Web bridge" than a complete "multi-channel personal Agent platform".

Main gaps:

- No unified Channel model comparable to CowAgent.
- Web/mobile is still a special entry point, not one member of a channel system.
- External messaging platforms are not yet part of the product.
- Scheduled jobs are not yet connected to unified channel delivery and personal reminders.
- Memory, tasks, skills, logs, and scheduling are not yet exposed as one coherent personal control center.
- The product narrative should stay centered on personal life and workflows, with local execution as supporting capability.

## 3. Product Principles

### 3.1 Follow CowAgent: unify channels before expanding the platform

One of CowAgent's key strengths is that Web, terminal, WeChat, Feishu, DingTalk, WeCom, Telegram, Slack, and other entry points are treated as channels.

AniyaAgent should follow the same product idea: each entry point is a channel. The channel receives messages and sends replies, while the actual Agent logic remains shared.

### 3.2 One Agent brain, many entry points

The user should not feel that "Web Agent", "mobile Agent", and "messaging Agent" are different systems.

Different entry points can have different interaction patterns, but they should share:

- Memory.
- Sessions.
- Tasks.
- Reminders.
- Skills.
- Tool capabilities.
- Permission policies.
- Audit records.

### 3.3 Personal workflows first

Following CowAgent's product direction, AniyaAgent should evolve from "can execute tools" to "can continuously help the user manage personal affairs".

Priority scenarios:

- Daily planning.
- Task breakdown and follow-up.
- Scheduled reminders.
- Project tracking.
- Knowledge capture.
- File and information organization.
- Personal review.
- Long-term goal management.

### 3.4 Web console as the default management entry

CowAgent's Web console is not only a chat interface. It also manages models, channels, skills, memory, tasks, and runtime state.

AniyaAgent should similarly upgrade Web/mobile into a personal Agent control center, rather than keeping it as a simple chat page.

### 3.5 Keep local execution as a differentiator

CowAgent is stronger as a multi-channel platform. AniyaAgent is currently stronger in local execution and task runtime.

The future product should combine both:

- CowAgent-like multi-channel entry.
- AniyaAgent's local execution, permission, task, and testing foundation.
- A differentiated product: multi-channel personal entry plus local execution Agent.

## 4. Target Architecture

The target architecture follows CowAgent's layered product idea, adapted for AniyaAgent's personal Agent direction.

```text
User Entry Points
  - Web / Mobile
  - CLI
  - Messaging platforms
  - Scheduled jobs
  - Future email, webhook, desktop entries

        |
        v

Channel Layer
  - Each entry point connects independently
  - Messages normalize into internal requests
  - Replies render into channel-specific formats
  - Channel lifecycle can be managed

        |
        v

Shared Agent Runtime
  - Session management
  - Queue and concurrency control
  - Cancellation
  - Audit records
  - Context management

        |
        v

Agent Capability Layer
  - Model calls
  - Tool calls
  - Memory
  - Tasks
  - Scheduling
  - Skills
  - Permissions
  - Multi-agent collaboration

        |
        v

Delivery Layer
  - Text replies
  - Files / images / voice
  - Proactive notifications
  - Permission approvals
  - Scheduled task results
```

## 5. Core Product Modules

### 5.1 Multi-channel system

Inspired by CowAgent, channels should become AniyaAgent's first product layer.

Channel responsibilities:

- Receive platform messages.
- Identify users, groups, and sessions.
- Convert platform messages into unified internal requests.
- Convert Agent replies into platform messages.
- Handle platform-specific files, images, voice, long text, and status.

Recommended channel priority:

- Web/mobile: default console and primary entry.
- CLI: local debugging and advanced-user entry.
- Scheduled jobs: system-triggered entry.
- One low-complexity external channel: to validate the multi-channel loop.
- Feishu / WeChat / WeCom: high-value Chinese work and life channels.

### 5.2 Web Control Center

Following CowAgent's Web console, AniyaAgent's Web/mobile experience should become the unified management surface.

Target pages:

- Chat: conversation entry.
- Tasks: personal tasks.
- Schedule: reminders and scheduled jobs.
- Memory: long-term memory.
- Skills: skill management.
- Channels: channel management.
- Logs: run and permission records.
- Settings: model, permission, safety, and workspace configuration.

The Web Control Center should be a personal Agent dashboard, not just a chat window.

### 5.3 Personal tasks and workflows

AniyaAgent should evolve from "can complete one request" to "can continuously manage user affairs".

Using CowAgent's combination of tasks, memory, scheduling, and skills as a reference, AniyaAgent should support:

- Turning conversation items into tasks.
- Scheduling tasks into future reminders.
- Using memory to fill missing context.
- Generating next actions from project state.
- Running periodic personal reviews.
- Organizing files, references, web pages, and chat records into knowledge.

### 5.4 Memory and knowledge

CowAgent's value is not only in channels, but also in long-term context.

AniyaAgent's memory should serve personal growth and personal affairs management.

Memory should cover:

- User preferences.
- Long-term goals.
- Current projects.
- People and relationships.
- Reusable routines.
- Important decisions.
- User feedback about Agent behavior.

Memory product principles:

- Visible to the user.
- Editable by the user.
- Reversible by the user.
- Explainable when the Agent uses important memories.

### 5.5 Scheduling and proactive reach

Following CowAgent's scheduled tasks and multi-channel capabilities, AniyaAgent should not only answer passively. It should proactively reach the user when useful.

Typical scenarios:

- Send a daily plan every morning.
- Generate an evening review.
- Remind the user to follow up with someone.
- Periodically check project status.
- Periodically summarize the knowledge base.
- Warn before tasks become overdue.

Scheduling must connect with channel delivery: when a task finishes, the result should be delivered through the user's chosen entry point.

### 5.6 Safety and trust

AniyaAgent has stronger local execution, so safety matters more than in a normal chat Agent.

After adopting a CowAgent-like multi-channel shape, channel trust levels become important:

- Local Web/CLI can handle high-trust operations.
- External messaging channels should default to low-risk tasks.
- High-risk tools require user approval.
- Permission prompts should clearly explain the reason and impact.
- Key actions should be auditable.

## 6. MVP Roadmap

### Phase 0: Product alignment

Goal: align AniyaAgent as a CowAgent-inspired personal multi-channel Agent platform.

Outcome:

- Product positioning is clear.
- CowAgent is the primary reference.
- The gap between current and target capabilities is clear.
- Future work centers on channels, console, and personal workflows.

### Phase 1: Productize the Web entry

Goal: upgrade Web/mobile from a simple chat entry into a personal control center.

CowAgent reference:

- Web is the default entry.
- Web manages configuration and capabilities.
- Web manages channels.
- Web shows tasks, memory, skills, and runtime status.

Outcome:

- The user can understand and manage the Agent from Web.
- Web is no longer only a prompt submission page.

### Phase 2: Unified Channel model

Goal: make every AniyaAgent entry follow the CowAgent-style Channel idea.

Outcome:

- Web, CLI, scheduled jobs, and external platforms are all treated as channels.
- Channels share the same Agent runtime.
- The path for adding new channels is clear.
- Channels can be started, stopped, configured, and observed.

### Phase 3: External channel validation

Goal: connect the first real external channel and validate AniyaAgent as a multi-channel Agent platform.

Suggested priority:

- Start with a simple, stable, low-debugging-cost channel.
- Then add Feishu, WeChat, or WeCom for high-value Chinese workflows.

Outcome:

- The user can use AniyaAgent from daily communication tools, not only Web.
- Multi-channel sessions, tasks, and memory can be shared.

### Phase 4: Personal workflow maturity

Goal: make AniyaAgent move beyond chat and execution into continuous personal assistance.

Outcome:

- The user can naturally create tasks and reminders.
- The Agent can produce daily plans and reviews.
- Memory continuously influences answers and task management.
- Scheduled tasks proactively deliver results.
- The Web console shows the user's complete personal workflow.

### Phase 5: Long-term personal platform

Goal: make AniyaAgent reliable as a long-running personal Agent platform.

Outcome:

- Multi-channel access is stable.
- Memory is trusted.
- Tasks and reminders are reliable.
- Permissions and audit records are clear.
- The user can rely on it for long-term personal affairs management.

## 7. Near-Term Product Priorities

No implementation details here. Priorities should be ordered by product value:

1. Adopt a CowAgent-style multi-channel platform direction.
2. Define Web/mobile as the default control center.
3. Treat channels as the unified entry model.
4. Combine tasks, memory, scheduling, and skills into a personal workspace.
5. Validate one external channel before expanding to high-value Chinese channels.
6. Keep local execution and permission control as AniyaAgent's differentiators.

## 8. Product Metrics

To measure whether AniyaAgent is moving toward a CowAgent-like personal platform, track:

- Whether the user uses the Agent from multiple entry points.
- Whether the user continuously creates and completes tasks.
- Whether scheduled jobs reliably reach the user.
- Whether memory is inspected, corrected, and trusted by the user.
- Whether the Web console becomes the primary management entry.
- Whether external channels carry real daily workflows.
- Whether daily personal workflows become the main usage pattern across channels.

## 9. Key Risks

- Copying CowAgent's number of channels without creating a unified product experience.
- Connecting complex channels too early before the core model is stable.
- A weak Web console that cannot manage memory, tasks, and channels.
- Local execution being too powerful for external-channel triggers.
- Opaque memory causing the user to distrust the Agent.
- The product drifting back toward an execution demo instead of staying focused on personal workflows.

## 10. Strategic Recommendation

AniyaAgent should reference CowAgent, but not simply copy it.

What to learn from CowAgent:

- Multi-channel abstraction.
- Web console.
- Unified message handling.
- Memory, tasks, skills, and scheduling as one system.
- Product direction around daily personal entry points.

What AniyaAgent should preserve:

- Local execution capability.
- Permission control.
- Task runtime.
- Tests and benchmarks.
- Extensibility for deep personal workflows.

Final target:

> A CowAgent-inspired multi-channel personal Agent platform, with stronger emphasis on local execution, safety controls, and long-term personal workflows.
