---
name: aniyaagent-agent
description: Guidelines for extending the AniyaAgent learning agent.
---

# AniyaAgent Agent

Use this skill when modifying the local AniyaAgent Python agent.

## Rules

- Keep `main_loop.py` focused on orchestration.
- Put tool implementations in `Tools.py`.
- Put cross-cutting mechanisms such as skills, compaction, and memory into separate modules.
- Prefer small, readable teaching implementations over framework-heavy abstractions.
- After adding a mechanism, verify imports before running an interactive agent session.
