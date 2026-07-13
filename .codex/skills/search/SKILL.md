---
name: search
description: Internal Search Mode engine for /goal-plus with the goal-plus MCP server from Codex.
---

# Search Mode Runtime for Codex

Use this skill after `/goal-plus` has upgraded a goal to Search Mode, or for
explicit low-level debugging of an already measurable SearchSpec. The normal
user-facing entrypoint is `/goal-plus`.

Use the logical `search_*` tools exposed by the `goal-plus` MCP server.
Codex may display MCP tools with a client-specific prefix; match by the final
logical tool name.

## Verifier Freeze Contract

Before `search_freeze_spec`, run the proposed `ranking_signal` from
`source_path` and confirm its final non-empty stdout line is JSON with a finite
numeric `spec.metric_name`, for example `{"combined_score": 123.0}`. The
command may be inline or call an existing repository tool. Create a custom
verifier file only when needed and materialize it during Spec Discovery before
freezing, in a source-owned path such as `.goal-plus-verifiers/`, never `.gp/`
or `.search/`. The freeze tool exposes the complete nested `SearchSpec` schema.
`expected_outputs` accepts artifact path/glob strings only and does not parse
stdout. The runtime repeats this preflight and rejects an invalid freeze before
any candidate starts.

## Main Workflow

1. Call `search_freeze_spec` for the Goal Plus spec draft, or `search_create`
   when a frozen spec already exists.
2. Call `search_plan_next`.
3. Call `search_start_batch`.
4. For each new candidate, call `search_start_agent_session`.
5. Launch a foreground Codex subagent with the returned launch payload:
   - Project the payload onto the current `spawn_agent` tool schema. Always pass
     `task_name`, `message`, and `fork_turns` when those fields are exposed.
   - Pass optional `agent_type`, `model`, `reasoning_effort`, or `service_tier`
     metadata only when the current tool schema exposes the corresponding
     field. Some Codex configurations intentionally hide this metadata.
   - Do not fail merely because optional launch metadata is hidden. When no
     model override can be passed, the worker inherits the parent Codex model.
6. If `spawn_agent` returns a task name or nickname, call `search_bind_agent_handle` with:
   - `host: "codex"`
   - `task_name`
   - `nickname` when present
7. If `launch.budget_control.mode == "parent_watchdog"`, enforce the worker
   deadline from the parent agent:
   - launch the worker first with `spawn_agent(...)`
   - wait first with
     `wait_agent(timeout_ms=launch.budget_control.initial_wait_timeout_ms)`
   - if that initial wait times out, send exactly one soft closeout using
     `send_message(target=launch.budget_control.closeout_target,
     message=launch.budget_control.closeout_message)`
   - wait again with
     `wait_agent(timeout_ms=launch.budget_control.final_wait_timeout_ms)`
   - if the final wait times out and `on_exceed == "interrupt"`, call
     `interrupt_agent(target=launch.budget_control.interrupt_target)`
   - after interruption, call `wait_agent` once more to observe the final state
   - merge `timed_out`, `soft_closeout_sent`, the final assistant summary, and
     `.tmp/handoff.json` when present by calling `search_bind_agent_handle`
     again for the same `agent_session_id`
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
control for Codex is the sum of `budget_control.initial_wait_timeout_ms` and
`budget_control.final_wait_timeout_ms`, followed by interruption. The
`soft_closeout_seconds` field records the closeout window; it is not a
runtime-owned worker timer.

Project `PostToolUse` hooks also provide a separate, advisory-only timing
signal to Search candidate subagents. After `search_get_agent_context` binds
the worker identity, each subagent tool completion may compare the available
worker/outer-task time with the observed average verifier-submission time. The
hook injects at most one message per `agent_session_id`, lists each sampled
candidate's elapsed time and verifier count, and never stops the worker. Main
agent, ordinary subagent, and final-checker PostTool events must not trigger it.
An outer harness may set `GOAL_PLUS_OUTER_DEADLINE_AT` to an RFC 3339 timestamp
or Unix epoch; otherwise the worker budget is used when available.

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
