---
name: search
description: Run agentic any-search with the search-runtime MCP server from Codex.
---

# Search Runtime for Codex

Use this skill when the user asks to run or continue an agentic search.

Use the logical `search_*` tools exposed by the `search-runtime` MCP server.
Codex may display MCP tools with a client-specific prefix; match by the final
logical tool name.

## Main Workflow

1. Call `search_freeze_spec` or `search_create` according to the user's input.
2. Call `search_plan_next`.
3. Call `search_start_batch`.
4. For each candidate, call `search_start_agent_session`.
5. Launch a foreground Codex subagent with the returned launch payload:
   - `spawn_agent(task_name=launch.task_name, agent_type=launch.agent_type, message=launch.message, fork_turns=launch.fork_turns)`
6. If `spawn_agent` returns a task name or nickname, call `search_bind_agent_handle` with:
   - `host: "codex"`
   - `task_name`
   - `nickname` when present
7. If `launch.budget_control.mode == "parent_watchdog"`, enforce the worker
   deadline from the parent agent:
   - launch the worker first with `spawn_agent(...)`
   - wait for completion or activity with `wait_agent(timeout_ms=launch.budget_control.wait_timeout_ms)`
   - if the wait times out and `launch.budget_control.on_exceed == "interrupt"`, stop the worker
   - prefer `interrupt_agent(target=launch.budget_control.interrupt_target)` when the tool is available
   - if this Codex surface exposes interruption through `send_input`, use `send_input(..., interrupt=true)` with a short stop message
   - after interrupting, call `wait_agent` once more to observe the final stopped/completed status
8. If no `budget_control` is present, wait for candidate workers according to Codex foreground subagent behavior.
9. Run final `search_run_verifier` from the main agent before selecting.
10. Use `search_select`, `search_report`, and `search_promote` when appropriate.

## Worker Budget Control

`budget_control.mode == "parent_watchdog"` means the runtime expects the parent
Codex agent to enforce elapsed worker time. Codex `spawn_agent` does not accept
a timeout argument, so the parent must combine `wait_agent` with an interrupt.

Treat `budget_control.max_turns_hint` as a prompt-level hint only. The hard
control for Codex is `budget_control.wait_timeout_ms` plus interruption.

## Continuation

Codex does not expose an equivalent same-worker continuation in this adapter.
If `search_continue_agent_session` reports unsupported capability for Codex,
start a new foreground Codex worker for the same candidate and include the prior
context from `search_get_agent_context`.
