---
name: goal-plus-with-final-check
description: Run Goal Plus with a required independent Codex final checker before completion.
---

# Goal Plus With Final Check

Use this skill for `/goal-plus-with-final-check` or
`$goal-plus-with-final-check`. The Codex `UserPromptSubmit` hook creates the
Goal Plus record with `policy.final_check.mode="required"` before the model
turn.

Follow the complete `goal-plus` skill workflow. The only additional terminal
contract is mandatory:

1. Do not call `goal_plus_set_status(status="complete")` yourself.
2. After the implementation and raw-goal audit are finished, call
   `goal_plus_prepare_final_check(goal_plus_id, checker_host="codex")`.
3. Project the returned launch payload onto the available foreground
   `spawn_agent` tool. Use the returned `task_name`, `message`, `fork_turns`,
   and `agent_type` when that field is exposed.
4. Wait for the checker to return. A passing checker atomically completes the
   Goal Plus record. A failure requires fixing every finding and requesting a
   fresh check. An interrupted checker leaves the goal active and also requires
   a fresh check.
5. Read `goal_plus_status`, then call `goal_plus_gate(event="stop")` before
   stopping.

`/goal-plus edit <full revised goal>` keeps the same Goal Plus id, creates a new
goal revision, and invalidates every older check. `/goal-plus resume` continues
the current durable revision after a host interruption.
