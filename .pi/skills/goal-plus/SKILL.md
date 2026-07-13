---
name: goal-plus
description: Use when Pi receives a /goal-plus, /goal-plus edit, or /goal-plus-with-final-check request that may need Goal Mode, Spec Discovery Mode, bounded Search Mode, or an independent final reviewer.
---

# Goal Plus For Pi

## Entry Contract

The native Pi `/goal-plus` command creates the Goal Plus record before the model turn starts. `/goal-plus-with-final-check` creates it with `policy.final_check.mode="required"`. `/goal-plus edit <full revised goal>` calls `goal_plus_update_goal` for the active record and increments `goal_revision`; the latest raw goal supersedes older revisions. `/goal-plus resume` continues the same durable active revision after an interrupted Pi turn. If a compatibility prompt path is used and no active `goal_plus_id` is already present, the first tool call must be `goal_plus_create(raw_goal=...)`. Do not triage, search, or edit before the goal record exists. Except for loading the goal-plus skill, do not read or audit target files before `goal_plus_record_triage`.

Before resuming an active record, treat the latest user message as authoritative
for this turn. Keep the current revision when the message only continues or
steers the existing objective. If it changes the effective scope, deliverables,
or success criteria, call `goal_plus_update_goal` with the complete revised
objective and current `expected_revision`, then re-triage before further work.
If it is unrelated, respond without changing the goal. If its relationship to
the goal is unclear, clarify before revising or resuming; do not resume merely
because the Goal Plus record is active.

## Goal Mode

Use Goal Mode when the request is not yet a verifiable optimization/search task. Record triage with `goal_plus_record_triage({ goal_plus_id, triage: { is_optimization, confidence, recommended_phase, identified_at, scenario, reasons, missing } })` and keep the user-facing goal separate from implementation guesses. Do not create a SearchSpec in Goal Mode.

If the raw goal explicitly requests verifier-guided Search Mode and supplies a
measurable verifier or metric, classify it as optimization/search; do not
downgrade it to ordinary Goal Mode merely because the requested run is small.

## Spec Discovery Mode

Use Spec Discovery Mode when the target needs a frozen verifier or edit surface. Save candidate details with `goal_plus_save_spec_draft`. Once the draft is high-confidence with no open questions, upgrade to Search Mode automatically. Do not ask the user to approve the verifier, metric, edit surface, promotion rule, or mode change. User hints are useful but optional; discover missing details from the workspace and decide from evidence.

A ranking verifier must emit a final JSON object with a finite numeric
`spec.metric_name`, for example `{"combined_score": 123.0}`. Its command may
be inline or call an existing repository tool. Create a custom verifier file
only when needed; materialize it with the available host tools during Spec
Discovery and before `search_freeze_spec`, in a source-owned path such as
`.goal-plus-verifiers/`, never `.gp/` or `.search/`. Spec Discovery permits
the `bash`, `write`, and `edit` work needed to inspect the public contract and
materialize that file. `expected_outputs` lists artifact paths/globs and is not
a stdout parser. The Pi freeze tool exposes the complete nested `SearchSpec`
schema; fill it directly rather than guessing fields from validation errors.
`search_freeze_spec` repeats verifier preflight and rejects the spec before
candidate workers start when the contract is invalid.

## Search Mode

When the goal is search-ready:

`origin="initial"` and `origin="in_progress"` are provenance only and follow
the same autonomous admission rule. The legacy
`goal_plus_confirm_frozen_verifier` tool and
`user_confirmed_frozen_verifier` field remain compatible with older runs, but
they are optional audit evidence and must never pause `/goal-plus`.

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

### Search Run Budget Planning

Choose the whole-run candidate budget before `search_freeze_spec`; it is frozen
and cannot grow inside that run. `budget.max_candidates` is the total number of
distinct candidate workspaces across all rounds. `budget.max_parallel` is only
the maximum width of one planned batch. Therefore the planned round capacity is
approximately `ceil(max_candidates / max_parallel)`. If the two values are
equal, the run normally has only one full batch.

When the user or outer harness supplies a wall-clock, attempt, or token budget:

1. Reserve time for main-agent final verification, selection, reporting, and
   promotion.
2. Choose a batch width `max_parallel` that the host can support. When no better
   resource signal exists, recommend 4; this is a planning recommendation, not
   a runtime default.
3. Estimate one batch duration from `worker_budget.max_runtime_seconds`, prior
   observed worker durations, and Pi launch/verifier overhead. Under actual
   concurrent `pi_search_run_batch` execution use the slowest worker duration,
   not the sum of all workers.
4. Estimate `rounds = floor((remaining_seconds - final_reserve_seconds) /
   estimated_batch_seconds)`, subject to explicit attempt/token caps, then set
   `max_candidates = rounds * max_parallel`. Keep at least one candidate only
   when enough time remains to produce and verify useful work.

For example, 7200 seconds remaining, 900 seconds reserved, and 1260 seconds per
batch gives 5 rounds; with `max_parallel=3`, set `max_candidates=15`.

After every completed batch, refresh remaining time and
`search_list_history` before calling `search_plan_next` again. `requested_k` is
only the request for that round; use at most `max_parallel` and the remaining
total candidate budget. Do not treat its default value 4 as the whole-run
budget. Do not call `search_select` while another useful batch fits the
remaining budget. Select only when the candidate cap is exhausted, another
batch no longer fits before the final reserve, an explicit attempt/token cap is
reached, or a declared early-stop condition holds.

1. `search_freeze_spec`, or reuse an existing `frozen_spec_id` when the later
   cycle keeps the same verifier and edit contract
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
10. Run the raw-goal audit. If another verifier-backed search is needed,
    freeze/create and link a new `run_id`, then repeat the Search Mode flow
    under the same `goal_plus_id`.
11. Run the final raw-goal audit. For a normal record, finish with
    `goal_plus_set_status`. If `policy.final_check.mode="required"`, instead:
    - call `goal_plus_prepare_final_check(checker_host="pi")`
    - pass the exact returned `launch` object to
      `pi_goal_plus_run_final_check`
    - let the stateless, read-only Pi reviewer call
      `goal_plus_submit_final_check` itself
    - address failed findings and prepare a fresh check
    A passing required check atomically marks the record complete.

One Goal Plus record is the complete user task. `search_tasks` is its
append-only search-task history; each item is one `run_id` over one frozen
spec. `linked_search` is only the current-task compatibility view. A search
task may contain multiple planning/search rounds.

`goal_revisions` and `final_checks` are append-only histories. An interrupted
Pi turn keeps the active Goal Plus id; the next session/turn restores it from
native state. A checker process exit or timeout records that attempt as
`interrupted`; call `goal_plus_prepare_final_check` to launch a fresh attempt.
Editing the goal supersedes pending checks and
requires fresh triage and a new check, while older Search tasks remain audit
history only.

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
main Pi agent unless `GOAL_PLUS_PI_EXPOSE_LOW_LEVEL_WORKER=1` is set
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

Each worker also leaves a bounded `progress_handoff` in its bound handle. It
combines the optional `.tmp/handoff.json` recovery note with a runner-owned Git
and verifier snapshot. `search_get_agent_context` exposes it under
`context.resume`; use this explicit resume object instead of relying on a Pi
transcript or on whether the candidate appears in top-N history.

When the previous attempt has no useful verifier evidence but its handoff shows
real progress, the main agent may redispatch once with `runtime_multiplier`
greater than 1 and at most 2. This scales only the frozen Pi
`max_runtime_seconds` for that fresh launch; it does not mutate the spec.

Do not redispatch only because the worker handle has `timed_out=true`. When the
candidate already has a `process_passed=true` Git-backed iteration, that best
iteration remains valid search evidence and eligible for later planning and
selection. Official history reports that best evidence in `score` and
`best_iteration`, while `latest_score` and `latest_process_passed` preserve a
later timeout or regression for diagnosis.

History is runtime-owned, not a local plan file. Workers must call
`search_get_agent_context` first and use `context.resume`, `context.history`,
and `context.iterations` as the resume source.

For optimization tasks, require workers to create a complete candidate artifact
and run an early `search_run_verifier` before any long local optimization loop.
For fix/target tasks, require the allowed-file edit before the verifier call; do
not count verification of the unmodified starting point as worker evidence.
`search_run_verifier` automatically commits changed candidate artifact files in
the candidate workspace before running the verifier, so search progress must be
visible as verifier-recorded runtime iterations with real `git_head` values, not
hidden in the worker transcript or scratch scripts.

Pi RPC workers also check an advisory time estimate after completed worker
tools. Once verifier evidence exists, the runner compares available worker or
outer-task time with `(last subagent verifier - first candidate session) /
subagent verifier count`, aggregated across sampled candidates. If one average
submission no longer fits, it sends one informational `steer` to that Search
candidate only. It does not stop the worker or replace the hard watchdog. Set
`GOAL_PLUS_OUTER_DEADLINE_AT` in the outer harness to an RFC 3339 timestamp or
Unix epoch when an end-to-end deadline is available.

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

The monitor summarizes durable `.gp` evidence including goal status, all
linked search tasks, per-task planning/started round counts, aggregate task,
candidate, worker-session, verifier, and Pi cost counts, selected candidate,
selected commit, report and promotion paths,
candidate scores, per-iteration git heads, agent sessions, verifier iterations,
one-shot time-advisory evidence, Pi RPC token/cost/context metrics, and
stale/timed-out warnings. It does not
start, wait for, or stop workers.

If the MCP tool is not directly exposed in the current host, use the matching
Pi facade instead of manually tailing state files:

```bash
goal-plus-pi-tool goal_plus_monitor_snapshot \
  --root .gp \
  --args-json '{"goal_plus_id":"gp_...","run_id":"run_...","stale_after_seconds":120}' \
  --pretty
```

Read raw `.gp/` files or host logs only when the monitor output is missing
the field you need, or when debugging a specific transcript, verifier log, or
host failure. Do not use manual file tailing as the primary monitoring path.
