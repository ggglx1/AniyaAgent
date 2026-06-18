---
name: happyclaude-agent
description: Guidelines for extending the HappyClaude learning agent.
---

# HappyClaude Agent

Use this skill when modifying the local HappyClaude Python agent.

## Rules

- Keep `MainLoop.py` focused on orchestration.
- Put tool implementations in `Tools.py`.
- Put cross-cutting mechanisms such as skills, compaction, and memory into separate modules.
- Prefer small, readable teaching implementations over framework-heavy abstractions.
- After adding a mechanism, verify imports before running an interactive agent session.
