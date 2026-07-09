---
name: goal-plus
description: Use when Pi receives a /goal-plus request that may need Goal Mode, Spec Discovery Mode, or bounded Search Mode.
---

# Goal Plus For Pi

## Entry Contract

The native Pi `/goal-plus` command creates the Goal Plus record before the model turn starts. If a compatibility prompt path is used and no active `goal_plus_id` is already present, the first tool call must be `goal_plus_create(raw_goal=...)`. Do not triage, search, or edit before the goal record exists. Except for loading the goal-plus skill, do not read or audit target files before `goal_plus_record_triage`.

## Goal Mode

Use Goal Mode when the request is not yet a verifiable optimization/search task. Record triage with `goal_plus_record_triage({ goal_plus_id, triage: { is_optimization, confidence, recommended_phase, identified_at, scenario, reasons, missing } })` and keep the user-facing goal separate from implementation guesses. Do not create a SearchSpec in Goal Mode.

## Spec Discovery Mode

Use Spec Discovery Mode when the target needs a frozen verifier or edit surface. Save candidate details with `goal_plus_save_spec_draft`; if the verifier is already frozen and trustworthy, call `goal_plus_confirm_frozen_verifier` with evidence.

## Search Mode

When the goal is search-ready:

1. `search_freeze_spec`
2. `search_create`
3. `goal_plus_link_search_run`
4. `search_plan_next`
5. `search_start_batch`
6. For each candidate, call
   `pi_search_run_candidate(run_id, candidate_id, directive?, final_verify=true)`.
7. Review the returned `steps`, `handle.metadata.pi_metrics`, and
   `final_score_report`.
8. Call `search_select`, `search_report`, and `search_promote` when promotion is
   requested.
9. Call `goal_plus_record_search_result`.
10. Run the final raw-goal audit and then `goal_plus_set_status`.

`pi_search_run_candidate` automatically performs the mechanical worker chain:
`search_start_agent_session`, `pi_rpc_run_worker`,
`search_bind_agent_handle`, and the final `search_run_verifier` without
`agent_session_id` when `final_verify=true`. Use the low-level tools directly
only for manual debugging, custom recovery, or a deliberate same-session
continuation path.

Worker launch is foreground and synchronous. `worker_budget.max_runtime_seconds`
is required and maps to the Pi RPC process watchdog. `worker_budget.max_turns`
is only a prompt hint.

Continuation uses `session_jsonl_restart`: `search_continue_agent_session`
returns another `pi_rpc_run_worker` launch using the same Pi `--session-id`; it
is not a live stdin continuation. If a worker times out or exits before
producing useful verifier evidence, prefer `search_redispatch_candidate` to
create a new `agent_session_id` for the same candidate workspace.

History is runtime-owned, not a local plan file. Workers must call
`search_get_agent_context` first and use `context.history` plus
`context.iterations` as the resume source.

For optimization tasks, require workers to create a complete candidate artifact
and run an early `search_run_verifier` before any long local optimization loop.
For fix/target tasks, require the allowed-file edit before the verifier call; do
not count verification of the unmodified starting point as worker evidence.
Search progress must be visible as verifier-recorded runtime iterations, not
hidden in the worker transcript or scratch scripts.

## Skill Boundary

Pi exposes `goal-plus` as the complete user-facing skill. Do not split Search
Mode or scenario-specific optimization guidance into additional visible Pi
skills. Keep domain constraints in the raw user goal, target workspace docs, or
example documentation, and let Goal Plus discover the verifier-backed
SearchSpec before opening Search Mode.

## Gates

Before Search Mode tool use and main-agent mutating tools (`bash`, `edit`, `write`, `pi_rpc_run_worker`), Pi's extension calls `goal_plus_gate(event="pre_tool_use")`. At turn end, the extension calls `goal_plus_gate(event="stop")`; if the gate blocks, it queues the continuation prompt and triggers another model turn. If the extension is unavailable, manually call the same gates and follow their allow/block decisions.

## Monitoring

For active or completed Goal Plus/Search runs, use
`goal_plus_monitor_snapshot(goal_plus_id?, run_id?, stale_after_seconds?)`
first. It is the primary read-only monitoring path.

The monitor summarizes durable `.search` evidence including goal status, linked
run state, selected candidate, report and promotion paths, candidate scores,
agent sessions, verifier iterations, Pi RPC token/cost/context metrics, and
stale/timed-out warnings. It does not start, wait for, or stop workers.

If the MCP tool is not directly exposed in the current host, use the matching
Pi facade instead of manually tailing state files:

```bash
agentic-any-search-pi-tool goal_plus_monitor_snapshot \
  --root .search \
  --args-json '{"goal_plus_id":"gp_...","run_id":"run_...","stale_after_seconds":120}' \
  --pretty
```

Read raw `.search/` files or host logs only when the monitor output is missing
the field you need, or when debugging a specific transcript, verifier log, or
host failure. Do not use manual file tailing as the primary monitoring path.
