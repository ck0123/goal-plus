# Worker Budget Smoke Evidence

This file records the manual smoke tests used to verify Codex and Claude Code
worker budget behavior for the adapter implementation. Raw logs are under the
gitignored `.search/smoke-logs/` directory in this workspace.

## Codex Parent Watchdog

Command log: `.search/smoke-logs/codex-worker-budget.jsonl`

Observed behavior:

- The parent Codex run received `budget_control.mode = "parent_watchdog"`.
- It spawned one child worker for a task that ran `sleep 60`.
- It waited for the 10 second watchdog window.
- The wait timed out.
- The parent interrupted the child through the available fallback
  `send_input(interrupt=true)` surface.
- The final child status was completed with a message that `sleep 60` was
  interrupted and did not complete.

Key log evidence:

```text
budget_control parent_watchdog: wait timed out after 10s; interrupt succeeded via send_input(interrupt=true).
Final child status: completed - sleep 60 was interrupted/aborted and did not complete.
```

## Claude Code Subagent Max Turns

Command log: `.search/smoke-logs/claude-subagent-budget.jsonl`
Debug log: `.search/smoke-logs/claude-subagent-budget-debug.log`

Observed behavior:

- The parent Claude Code run launched a real subagent with
  `subagent_type = "budget_probe"`.
- The subagent definition used `maxTurns = 1`.
- The subagent executed only one Bash tool call from a task that required two
  sequential Bash calls and a final response.
- The debug log recorded the turn budget being reached.

Key debug-log evidence:

```text
[API REQUEST] /api/anthropic/v1/messages source=agent:custom:budget_probe
[Agent: budget_probe] Reached max turns limit (1)
```

The top-level `claude -p --agent budget_probe` path was also tried and did not
serve as valid evidence for worker budget enforcement: it completed with
`num_turns: 3`. For this project, `maxTurns` should be verified through actual
foreground subagent launches, matching the adapter path.
