---
name: search
description: Internal Search Mode engine for /goal-plus with the search-runtime MCP server from Codex.
---

# Search Mode Runtime for Codex

Use this skill after `/goal-plus` has upgraded a goal to Search Mode, or for
explicit low-level debugging of an already measurable SearchSpec. The normal
user-facing entrypoint is `/goal-plus`.

Use the logical `search_*` tools exposed by the `search-runtime` MCP server.
Codex may display MCP tools with a client-specific prefix; match by the final
logical tool name.

## Main Workflow

1. Call `search_freeze_spec` for the Goal Plus spec draft, or `search_create`
   when a frozen spec already exists.
2. Call `search_plan_next`.
3. Call `search_start_batch`.
4. For each new candidate, call `search_start_agent_session`.
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
9. If a worker stops before useful verifier evidence, call
   `search_redispatch_candidate(run_id, candidate_id, directive?,
   worker_agent_type?, worker_budget={"max_runtime_seconds": <larger seconds>, ...})`
   and launch the returned payload as a new foreground worker for the same
   candidate.
10. Run final `search_run_verifier` from the main agent before selecting.
11. Use `search_select`, `search_report`, and `search_promote` when appropriate.

## Worker Budget Control

`budget_control.mode == "parent_watchdog"` means the runtime expects the parent
Codex agent to enforce elapsed worker time. Codex `spawn_agent` does not accept
a timeout argument, so the parent must combine `wait_agent` with an interrupt.

Treat `budget_control.max_turns_hint` as a prompt-level hint only. The hard
control for Codex is `budget_control.wait_timeout_ms` plus interruption.

Choose the worker budget before freezing the spec. Codex does not expose a
hard per-subagent step tier like OpenCode, so the enforceable escalation is a
larger `worker_budget.max_runtime_seconds` for the next search run or a
redispatch. You may also override `worker_agent_type` when local Codex agent
variants exist, but that is prompt/agent selection, not a hard step cap. If a
watchdog stops a worker before it records any verifier iteration or usable
final score, do not repeat the same underpowered budget unless the user
explicitly wants a cheap probe.

## Runtime History And Resume

History is runtime-owned, not a `plan.md` file. The main agent reads prior
candidate results through `search_list_history`; workers recover state through
`search_get_agent_context`, which returns `context.history` and
`context.iterations`.

Codex does not expose an equivalent same-worker continuation in this adapter.
Use `search_redispatch_candidate` to start a new foreground Codex worker for
the same candidate, optionally overriding `worker_agent_type` and
`worker_budget.max_runtime_seconds` for that dispatch. The returned prompt
tells the worker to treat `search_get_agent_context` as the authoritative
resume context. Do not ask the worker to infer prior attempts from chat
transcript.

## Continuation

Same-worker continuation is not supported for Codex. State-level resume is
supported through `search_redispatch_candidate`, which creates a new
`agent_session_id` for the same candidate workspace and relies on MCP
history/iterations.
