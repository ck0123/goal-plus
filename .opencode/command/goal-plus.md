---
description: Run a goal with optional Agentic Search upgrade
agent: goal-plus-orchestrator
subtask: false
---
Use the `goal-plus` skill to run this objective.

Before taking action:
1. Load the `goal-plus` skill with the skill tool.
2. Treat @.opencode/skills/goal-plus/SKILL.md as the required workflow reference.
3. Start by calling `goal_plus_create` for the raw objective.
4. If the task upgrades to Search Mode, call the `search` skill and follow its workflow.
5. If the `goal-plus` skill or search-runtime MCP tools are unavailable, stop and report the missing dependency.

Goal:
$ARGUMENTS
