---
name: goal-plus
description: Use when Pi receives a /goal-plus request that may need Goal Mode, Spec Discovery Mode, or bounded Search Mode.
---

# Goal Plus For Pi

## Entry Contract

The native Pi `/goal-plus` command creates the Goal Plus record before the model turn starts. If a compatibility prompt path is used and no active `goal_plus_id` is already present, the first tool call must be `goal_plus_create(raw_goal=...)`. Do not triage, search, or edit before the goal record exists. Except for loading the goal-plus skill, do not read or audit target files before `goal_plus_record_triage`.

## Goal Mode

Use Goal Mode when the request is not yet a verifiable optimization/search task. Record triage with `goal_plus_record_triage({ goal_plus_id, triage: { is_optimization, confidence, recommended_phase, identified_at, scenario, reasons, missing } })` and keep the user-facing goal separate from implementation guesses. Do not create a SearchSpec in Goal Mode.

If the raw goal explicitly requests verifier-guided Search Mode and supplies a
measurable verifier or metric, classify it as optimization/search; do not
downgrade it to ordinary Goal Mode merely because the requested run is small.

## Spec Discovery Mode

Use Spec Discovery Mode when the target needs a frozen verifier or edit surface. Save candidate details with `goal_plus_save_spec_draft`; if the verifier is already frozen and trustworthy, call `goal_plus_confirm_frozen_verifier` with evidence.

## Search Mode

When the goal is search-ready:

Before `search_freeze_spec`, ensure the SearchSpec strategy sets
`worker_host: "pi-rpc"` and `worker_mode: "agent-session-pool"`. Pi Search Mode
must run workers through the Pi RPC driver. Do not omit `worker_host`; the
runtime default is OpenCode and is wrong for Pi.

Pi-supported strategy names are limited to the portable builtin subset:

- `agent_guided`, `agent`, or `default`
- `random` or `random_mode`

Only offer names from this subset when drafting a Pi SearchSpec. Do not silently
rewrite an already frozen unsupported strategy; let runtime validation reject
it and create a corrected draft before freezing instead.

1. `search_freeze_spec`
2. `search_create`
3. `goal_plus_link_search_run`
4. `search_plan_next`
5. `search_start_batch`
6. For a multi-candidate batch, call
   `pi_search_run_batch(run_id, candidate_ids, directive?, final_verify=true, max_parallel=<budget.max_parallel>)`.
   For a single candidate or manual recovery, call
   `pi_search_run_candidate(run_id, candidate_id, directive?, final_verify=true)`.
7. Review the returned `steps`, `handle.metadata.pi_metrics`, and
   `final_score_report` for each result.
8. Call `search_select`, `search_report`, and `search_promote` when promotion is
   requested. `search_select` ranks verifier-recorded iterations, checks out the
   best committed candidate `git_head`, and runs a main-agent final verifier on
   that exact commit before recording the selected candidate.
9. Call `goal_plus_record_search_result`.
10. Run the final raw-goal audit and then `goal_plus_set_status`.

Never invent `frozen_spec_id`, `run_id`, `plan_id`, `candidate_id`, or
`agent_session_id` values. Use only exact ids returned by the immediately
preceding runtime tool. In particular, call `search_create` before
`goal_plus_link_search_run` and link the exact returned `run_id`.

`pi_search_run_batch` runs candidate workers concurrently up to
`max_parallel`, then returns ordered per-candidate results. It is still a
foreground host driver: it does not add runtime-owned wait, abort, heartbeat,
or lifecycle supervision. `pi_search_run_candidate` performs the same chain for
a single candidate:
`search_start_agent_session`, `pi_rpc_run_worker`,
`search_bind_agent_handle`, and the final `search_run_verifier` without
`agent_session_id` when `final_verify=true`. Normal Goal Plus/Search flow must
not call the low-level `pi_rpc_run_worker` tool directly. It is hidden from the
main Pi agent unless `AGENTIC_ANY_SEARCH_PI_EXPOSE_LOW_LEVEL_WORKER=1` is set
for manual debugging or custom recovery.

Do not call `search_start_agent_session`, `search_bind_agent_handle`, or
`search_continue_agent_session` from the Pi main agent; the high-level driver
owns those mechanical steps. For another attempt on an existing candidate,
call `pi_search_run_candidate(..., redispatch=true)`. The driver then uses
`search_redispatch_candidate` internally and records that step before launching
the fresh stateless worker.

Worker launch is foreground and synchronous. `worker_budget.max_runtime_seconds`
is required and maps to the Pi RPC process watchdog. `worker_budget.max_turns`
is only a prompt hint.

Pi workers run with `--no-session`, so same-worker continuation is unsupported.
If a worker times out, fails, or exits before producing useful verifier
evidence, use state-level resume by calling
`pi_search_run_candidate(..., redispatch=true)`. The driver uses
`search_redispatch_candidate` internally, creates a new
`agent_session_id` for the same candidate workspace, and recovers prior work
from MCP history, verifier iterations, and Git state.

Do not redispatch only because the worker handle has `timed_out=true`. When the
candidate already has a `process_passed=true` Git-backed iteration, that best
iteration remains valid search evidence and eligible for later planning and
selection. Official history reports that best evidence in `score` and
`best_iteration`, while `latest_score` and `latest_process_passed` preserve a
later timeout or regression for diagnosis.

History is runtime-owned, not a local plan file. Workers must call
`search_get_agent_context` first and use `context.history` plus
`context.iterations` as the resume source.

For optimization tasks, require workers to create a complete candidate artifact
and run an early `search_run_verifier` before any long local optimization loop.
For fix/target tasks, require the allowed-file edit before the verifier call; do
not count verification of the unmodified starting point as worker evidence.
`search_run_verifier` automatically commits changed candidate artifact files in
the candidate workspace before running the verifier, so search progress must be
visible as verifier-recorded runtime iterations with real `git_head` values, not
hidden in the worker transcript or scratch scripts.

## Skill Boundary

Pi exposes `goal-plus` as the complete user-facing skill. Do not split Search
Mode or scenario-specific optimization guidance into additional visible Pi
skills. Keep domain constraints in the raw user goal, target workspace docs, or
example documentation, and let Goal Plus discover the verifier-backed
SearchSpec before opening Search Mode.

## Gates

Before Search Mode tool use and main-agent mutating tools (`bash`, `edit`,
`write`, and `pi_rpc_run_worker` when explicitly exposed for debugging), Pi's
extension calls `goal_plus_gate(event="pre_tool_use")`. At turn end, the
extension calls `goal_plus_gate(event="stop")`; if the gate blocks, it queues
the continuation prompt and triggers another model turn. If the extension is
unavailable, manually call the same gates and follow their allow/block
decisions.

## Monitoring

For active or completed Goal Plus/Search runs, use
`goal_plus_monitor_snapshot(goal_plus_id?, run_id?, stale_after_seconds?)`
first. It is the primary read-only monitoring path.

The monitor summarizes durable `.gp` evidence including goal status, linked
run state, selected candidate, selected commit, report and promotion paths,
candidate scores, per-iteration git heads, agent sessions, verifier iterations,
Pi RPC token/cost/context metrics, and stale/timed-out warnings. It does not
start, wait for, or stop workers.

If the MCP tool is not directly exposed in the current host, use the matching
Pi facade instead of manually tailing state files:

```bash
agentic-any-search-pi-tool goal_plus_monitor_snapshot \
  --root .gp \
  --args-json '{"goal_plus_id":"gp_...","run_id":"run_...","stale_after_seconds":120}' \
  --pretty
```

Read raw `.gp/` files or host logs only when the monitor output is missing
the field you need, or when debugging a specific transcript, verifier log, or
host failure. Do not use manual file tailing as the primary monitoring path.
