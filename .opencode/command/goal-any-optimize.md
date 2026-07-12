---
description: Legacy alias for /goal-plus optimization goals
agent: goal-plus-orchestrator
subtask: false
---

Use the `goal-plus` skill for this legacy optimization command. This command is
only a compatibility alias; `/goal-plus` is the canonical user entrypoint.

Before taking action:
1. Load the `goal-plus` skill with the skill tool.
2. Treat @.opencode/skills/goal-plus/SKILL.md as the required workflow reference.
3. Do not bypass `/goal-plus` triage, autonomous spec discovery, Search Mode gates, or final raw-goal audit.
4. Call the internal `search` skill only after Goal Plus enters Search Mode.
5. If the `goal-plus` skill or goal-plus MCP tools are unavailable, stop and report the missing dependency.

Goal:

$ARGUMENTS
