---
name: goal-plus
description: Use when Pi receives a /goal-plus, /goal-plus edit, or /goal-plus-with-final-check request that may need Goal Mode, Spec Discovery Mode, bounded Search Mode, or an independent final reviewer.
---

# Goal Plus For Pi

## Entry Contract

The native Pi `/goal-plus` command creates the Goal Plus record before the model turn starts. `/goal-plus-with-final-check` creates it with `policy.final_check.mode="required"`. `/goal-plus edit <full revised goal>` calls `goal_plus_update_goal` for the active record and increments `goal_revision`; the latest raw goal supersedes older revisions. `/goal-plus resume` continues the same durable active revision after an interrupted Pi turn. If a compatibility prompt path is used and no active `goal_plus_id` is already present, the first tool call must be `goal_plus_create(raw_goal=...)`. Do not triage, search, or edit before the goal record exists. Except for loading the goal-plus skill, do not read or audit target files before `goal_plus_record_triage`.

`/goal-plus mode=autonomous <goal>` selects substantial initial exploration
(about 15 minutes when elapsed-time leases are available) and longer
evidence-driven reinvestment, potentially up to about one hour.
`/goal-plus mode=probe <goal>` selects short feasibility, potential, and
blocker probes. Omitted mode defaults to `autonomous`; an edit without a mode
preserves the current choice. The runtime stores it only as a canonical final
line in `raw_goal`, not as a phase, Search strategy, or runtime field.

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

The freeze preflight runs in a disposable source copy and requires the verifier
to keep that workspace read-only. Put compiler products and temporary outputs
in the unique `GOAL_PLUS_VERIFIER_TMPDIR`/`TMPDIR` or a Python
`tempfile.TemporaryDirectory()`. Never use one fixed `/tmp` pathname because
`pi_search_run_batch` may verify several candidates concurrently. A
`VerifierWorkspaceSideEffect` must be repaired and refrozen before any Search
run uses candidate budget.

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
distinct candidate workspaces. `budget.max_parallel` is the hard cap on live Pi
candidate workers. A persisted Search round is a planning decision epoch, not a
barrier: the main agent may plan again as soon as any worker completes and a
pool slot becomes free.

When the user or outer harness supplies a wall-clock, attempt, or token budget:

1. Reserve time for main-agent final verification, selection, reporting, and
   promotion.
2. Choose `max_parallel` that the host can support. When no better
   resource signal exists, recommend 4; this is a planning recommendation, not
   a runtime default.
3. Set `max_candidates` as a conservative whole-run safety cap that fits the
   outer budget. It is not a required round count and need not be exhausted.
4. Give initial probes enough uninterrupted time to create real artifacts and
   verifier evidence. Reinvest more time in valuable directions and stop weak
   directions based on evidence and remaining time.

After every `candidate_ready`, failed, or interrupted pool event, refresh
remaining time and `search_list_history`. Decide independently whether each free
slot should continue a promising candidate, start a new direction, stay idle,
or begin final selection. `requested_k` is only the number of new candidate
workspaces desired at that decision point. Never wait for unrelated slow
workers merely to preserve a batch boundary.

Treat the frozen `strategy.worker_budget` as the normal per-worker budget, not
as a rule that every direction deserves identical depth. The main agent owns
reinvestment decisions. When a proposal is unusually promising or represents
a structurally distinct macro direction, assign a larger one-dispatch
`worker_budget` that still fits the outer remaining time. Do not encode a fixed
number of worker iterations or Search rounds as a substitute for this judgment.
A long worker may need roughly 10-15 meaningful verifier-recorded artifacts to
explore a direction, while an unpromising direction may stop earlier on
evidence. Preserve final-verification and closeout reserve in either case.

Follow the exploration line in `raw_goal`. In `probe` mode, return once
feasibility, potential, and blockers are credible, then decide whether to
deepen or redirect. In `autonomous` mode, give valuable directions substantial
renewable leases rather than cutting every worker into equal short attempts.
No worker lease ending completes the Goal Plus record.

1. `search_freeze_spec`, or reuse an existing `frozen_spec_id` when the later
   cycle keeps the same verifier and edit contract
2. `search_create`
3. `goal_plus_link_search_run`
4. Fill the initial slots with `search_plan_next` and `search_start_batch`.
5. Call `pi_search_pool_open(run_id, candidate_ids, directive?,
   worker_budgets?, final_verify=true, max_parallel=<budget.max_parallel>)`.
   This starts detached managed workers and returns a durable `pool_id`
   immediately.
6. Call `pi_search_pool_wait_any(pool_id, timeout_seconds=...)`. Process every
   returned event. `candidate_ready` means the driver has started/bound the
   agent session and completed the main-agent final verifier; raw process exit
   alone is not success.
7. Review each event's `result.steps`, `handle.metadata.pi_metrics`, and
   `final_score_report`. Also inspect every
   `handle.metadata.progress_handoff.model_handoff`: carry its `key_results`,
   scenario-specific `pitfalls`, `blockers`, and `next_steps` into the next
   decision's candidate proposals. Do not reduce the next decision to only the best
   score or copy raw transcripts.
8. Refill each free slot immediately with one deliberate action:
   - `pi_search_pool_continue` for state-level reinvestment in a promising
     candidate, optionally with a larger one-dispatch budget;
   - `search_plan_next(requested_k=<new direction count>)`,
     `search_start_batch`, then one `pi_search_pool_submit` per new candidate;
   - leave the slot idle; or
   - begin final drain.
   `pi_search_pool_snapshot(run_id=...)` rediscovers the pool after an
   interrupted main Pi turn; use `pool_id` for later exact snapshots. The
   supervisor never auto-refills because the main agent owns policy.
9. Call `pi_search_pool_close(mode="drain")` before selection, or
   `mode="interrupt"` when remaining work should be stopped. Then call
   `search_select`, `search_report`, and `search_promote` when promotion is
   requested. `search_select` ranks verifier-recorded iterations, checks out the
   best committed candidate `git_head`, and runs a main-agent final verifier on
   that exact commit before recording the selected candidate.
10. Call `goal_plus_record_search_result`.
11. Run the raw-goal audit. If another verifier-backed search is needed,
    freeze/create and link a new `run_id`, then repeat the Search Mode flow
    under the same `goal_plus_id`.
12. Run the final raw-goal audit. For a normal record, finish with
    `goal_plus_set_status`. If `policy.final_check.mode="required"`, instead:
    - call `goal_plus_prepare_final_check(checker_host="pi")`
    - pass the exact returned `launch` object to
      `pi_goal_plus_run_final_check`
    - let the stateless, read-only Pi reviewer call
      `goal_plus_submit_final_check` itself
    - address failed findings and prepare a fresh check
    A passing required check atomically marks the record complete.

The top-level stop gate blocks every still-active Goal Plus record and returns
the full current raw goal plus creation/check timestamps and elapsed time. Use
that prompt to audit all requirements and any time condition already present
in the goal. Continue if unfinished; otherwise record a truthful terminal
status before stopping. Do not invent a separate Goal Plus deadline.

### Post-result Spec Reassessment

After the first meaningful optimization result becomes available, do not infer
that the current frozen spec is adequate merely because its score beats the
baseline or improves by a large relative factor. Relative improvement is useful
evidence, but it does not prove that the raw goal is close to being satisfied,
that important failure modes are covered, or that deeper structural optimization
is unavailable. When an absolute target, acceptance threshold, success
criterion, or known upper bound is unavailable, state that uncertainty and
explicitly consider whether the apparent improvement could still be far from
useful success.

Use the existing raw-goal audit to consider the appropriate response:

- `upgrade_spec`: the current verifier, edit contract, or search directive is
  too weak or too narrow. Save a stronger draft, freeze a new spec, and create a
  new Search run. Never modify the prior frozen artifacts in place.
- `keep_spec_with_justification`: the current spec remains a credible proxy for
  the raw goal. State the evidence for keeping it and direct subsequent Search
  toward deeper or structurally different approaches rather than assuming that
  the current best-so-far neighborhood is sufficient.
- `revise_goal`: the effective scope, deliverables, or success criteria need to
  change. Call `goal_plus_update_goal` with the complete revised raw goal and
  current `expected_revision`, re-triage, then discover and freeze the spec for
  that revision.

These labels describe a main-agent decision inside the existing raw-goal audit;
they are not new runtime states, an additional workflow phase, or a user
approval checkpoint.

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

The `pi_search_pool_*` tools are a host-owned supervisor, not Search runtime
lifecycle APIs. Their durable state lives under `.gp/host-pools/pi/`. They
enforce `max_parallel`, return `wait_any` events, and survive a main Pi turn
disconnect. Each worker still performs the same chain:
`search_start_agent_session`, `pi_rpc_run_worker`,
`search_bind_agent_handle`, and the final `search_run_verifier` without
`agent_session_id` when `final_verify=true`. Normal Goal Plus/Search flow must
not call the low-level `pi_rpc_run_worker` tool directly. It is hidden from the
main Pi agent unless `GOAL_PLUS_PI_EXPOSE_LOW_LEVEL_WORKER=1` is set
for manual debugging or custom recovery.

Do not call `search_start_agent_session`, `search_bind_agent_handle`, or
`search_continue_agent_session` from the Pi main agent; the managed pool owns
those mechanical steps. For another attempt on an existing candidate, call
`pi_search_pool_continue`. The supervisor then uses
`search_redispatch_candidate` internally and records that step before launching
the fresh stateless worker.

Pool submission is non-blocking; the detached wrapper owns the foreground Pi
RPC child and its cleanup. `worker_budget.max_runtime_seconds` is required and
maps to the Pi RPC process watchdog. `worker_budget.max_turns` is only a prompt
hint. `pi_search_run_batch` and `pi_search_run_candidate` remain compatibility
and debugging helpers, but the batch helper intentionally waits for the slowest
worker and must not be used for rolling orchestration.

Pi workers run with `--no-session`, so same-worker continuation is unsupported.
If a worker times out, fails, or exits before producing useful verifier
evidence, use state-level resume by calling
`pi_search_pool_continue`. The driver uses
`search_redispatch_candidate` internally, creates a new
`agent_session_id` for the same candidate workspace, and recovers prior work
from MCP history, verifier iterations, and Git state.

Exception: never redispatch after `failure_class=VerifierWorkspaceSideEffect`,
`metrics.infrastructure_failure=true`, or
`metrics.candidate_action=stop_and_report`. The worker must stop without
cleaning or retrying, and the main agent must not spend another batch on the
same `frozen_spec_id`. Repair the source-owned verifier, freeze a new spec, and
create a new run. The host driver, not the MCP runtime, owns closing out any
siblings that are still executing in a concurrent batch.

Each worker also leaves a bounded `progress_handoff` in its bound handle. It
combines the optional `.tmp/handoff.json` recovery note with a runner-owned Git
and verifier snapshot. `search_get_agent_context` exposes it under
`context.resume`; use this explicit resume object instead of relying on a Pi
transcript or on whether the candidate appears in top-N history.

When a candidate remains valuable after its first attempt, the main agent may
redispatch it with an explicit larger `worker_budget`, for example
`pi_search_run_candidate(..., redispatch=true,
worker_budget={"max_runtime_seconds": <larger seconds>, ...})`. This creates a
fresh Pi process in the same candidate workspace with an uninterrupted budget
chosen from evidence and outer remaining time; it does not mutate the frozen
spec. `runtime_multiplier` remains a compatibility shortcut for a redispatch
between 1x and 2x, but it is not the depth policy and must not cap a justified
long exploration.

Do not redispatch only because the worker handle has `timed_out=true`. When the
candidate already has a `process_passed=true` Git-backed iteration, that best
iteration remains valid search evidence and eligible for later planning and
selection. Official history reports that best evidence in `score` and
`best_iteration`, while `latest_score` and `latest_process_passed` preserve a
later timeout or regression for diagnosis.

History is runtime-owned, not a local plan file. Workers must call
`search_get_agent_context` first and use `context.resume`, `context.history`,
and `context.iterations` as the resume source. Later-round history includes the
latest structured `research_summary` when a worker supplied a handoff, so use
its task-specific results and pitfalls rather than repeating failed variants.

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
