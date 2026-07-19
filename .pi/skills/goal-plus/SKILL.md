---
name: goal-plus
description: Use when Pi receives a /goal-plus, /goal-plus edit, or /goal-plus-with-final-check request that may need Goal Mode, Spec Discovery Mode, bounded Search Mode, or an independent final reviewer.
---

# Goal Plus For Pi

## Entry Contract

The native Pi `/goal-plus` command creates the Goal Plus record before the model turn starts. `/goal-plus-with-final-check` creates it with `policy.final_check.mode="required"`. `/goal-plus edit <full revised goal>` calls `goal_plus_update_goal` for the active record and increments `goal_revision`; the latest raw goal supersedes older revisions. `/goal-plus resume` continues the same durable active revision after an interrupted Pi turn. If a compatibility prompt path is used and no active `goal_plus_id` is already present, the first tool call must be `goal_plus_create(raw_goal=...)`. Do not triage, search, or edit before the goal record exists. Except for loading the goal-plus skill, do not read or audit target files before `goal_plus_record_triage`.

`/goal-plus mode=autonomous <goal>` selects substantial initial exploration
(about 15 minutes when elapsed-time leases are available) and renewable
same-candidate continuation, potentially up to about one hour.
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
the managed Pi pool may verify several candidates concurrently. A
`VerifierWorkspaceSideEffect` must be repaired and refrozen before any Search
run uses candidate budget.

For an AscendC Direct Invoke operator goal described by semantics, approximate
shapes/dtypes, and reference hints, record
`scenario="ascendc_direct_invoke"` and read
`examples/ascendc-direct-search/SPEC_DISCOVERY.md` completely. Follow its
request schema and source template. Run its `materialize_knowledge.py` with
`knowledge.sources.json` against exact pinned Git commits to generate the
task-local `_skills/`; never copy a live Skill directory. Treat the curated AKG
AscendC tree as primary knowledge and use only the declared CANNBot supplements
for uncovered operator families. The main agent generates the Golden, cases,
verifier, baseline, and SearchSpec. Before `search_freeze_spec`, use a JSON
Schema validator to validate the generated `_task/operator_request.json`
against `examples/ascendc-direct-search/request.schema.json`; JSON parsing or a
manual field checklist is insufficient, and validation failure blocks
freezing. Never require the user to run a task preparer, supply a task
directory, or write a verifier. Support Direct Invoke only; the generated
knowledge is read-only and cannot launch source Agent or Plugin workflows.

This scenario is self-contained. Do not invoke an external AscendC Agent,
plugin, or orchestration workflow.

## Search Mode

When the goal is search-ready:

`origin="initial"` and `origin="in_progress"` are provenance only and follow
the same autonomous admission rule.

Before `search_freeze_spec`, ensure the SearchSpec strategy sets
`worker_host: "pi-rpc"` and `orchestration_mode: "parallel_loops"`. Pi Search
Mode must run a fixed initial
set of autonomous candidate loops through the Pi RPC driver. Do not omit these
fields; legacy runtime defaults do not express this policy.

Pi-supported strategy names are limited to the portable builtin subset:

- `agent_guided`, `agent`, or `default`
- `random` or `random_mode`

Only offer names from this subset when drafting a Pi SearchSpec. Do not silently
rewrite an already frozen unsupported strategy; let runtime validation reject
it and create a corrected draft before freezing instead.

### Search Run Budget Planning

Choose the whole-run candidate budget before `search_freeze_spec`; it is frozen
and cannot grow inside that run. In normal `parallel_loops` execution, set
`budget.max_candidates` equal to `budget.max_parallel`: every initial candidate
workspace is one long-lived autonomous loop, and no later planning round or
quality-based replacement is created.

When the user or outer harness supplies a wall-clock, attempt, or token budget:

1. Reserve time for main-agent final verification, selection, reporting, and
   promotion.
2. Choose `max_parallel` that the host can support. When no better resource
   signal exists, recommend 4.
3. Give every initial loop enough uninterrupted time to create a real artifact
   and verifier evidence.
4. Derive each continuation budget from outer remaining time and final closeout
   reserve, never from whether the main agent likes that candidate.
5. Resume while no global stop fact is true. Global stop facts are an explicit
   success criterion, user stop, invalidated run, or insufficient remaining
   time for another worker turn plus closeout.

The subagent owns bottleneck analysis, hypothesis choice, feature transfer,
structural restart, and rebase decisions within its candidate workspace. The
main agent never sends a preferred technical direction. A low score, one
non-improving turn, or another candidate leading is not a stop or replacement
condition.

Follow the exploration line in `raw_goal`. In `probe` mode, the global policy
may stop after feasibility, potential, and blockers are credible. In
`autonomous` mode, give every active candidate renewable leases while outer
time remains. No worker lease ending completes the Goal Plus record.

1. `search_freeze_spec`, or reuse an existing `frozen_spec_id` when the later
   cycle keeps the same verifier and edit contract
2. `search_create`
3. `goal_plus_link_search_run`
4. Call `search_plan_next(requested_k=budget.max_parallel)` exactly once and
   `search_start_batch` exactly once to create all initial candidates. Runtime
   rejects a second plan in `parallel_loops` mode.
5. Call `pi_search_pool_open(run_id, candidate_ids, directive?,
   worker_budgets?, final_verify=true, max_parallel=<budget.max_parallel>)`.
   This starts detached managed workers and returns a durable `pool_id`
   immediately.
6. Call `pi_search_pool_wait_any(pool_id, timeout_seconds=...)`. Process every
   returned event. `candidate_ready` means the driver has started/bound the
   agent session and completed the main-agent final verifier; raw process exit
   alone is not success.
7. For every `candidate_ready`, read the prior and current best from
   `search_list_history` or `goal_plus_monitor_snapshot`. The pool's
   `final_verify=true` path has already run the parent completion verifier, so
   the durable best candidate/score is current. Inspect
   `handle.metadata.progress_handoff.model_handoff` and `verifier_assessment`
   for recovery or a concrete verifier failure, but do not use them to choose a
   next technical direction. Sparse diagnostics, a low score, or lack of
   improvement are not grounds to refreeze, stop, or replace the candidate. If
   the main agent confirms verifier contract,
   coverage, determinism, target-alignment, or infrastructure failure, execute
   this mandatory quiesce/refreeze sequence before any other search action:
   1. call `search_invalidate_run` with a concrete reason, summary, and evidence;
   2. call `pi_search_pool_close(pool_id, mode="interrupt")`;
   3. poll the exact pool until `active_count=0`, preserving terminal handoffs;
   4. repair or regenerate the source-owned verifier and freeze a new spec;
   5. call `search_create(new_frozen_spec_id, source_run_id=old_run_id)` and
      link the successor run under the same `goal_plus_id`.
   Never select or promote the invalidated run. Its artifacts, scoped pitfalls,
   and features remain research input, but every old score is historical and
   every imported feature must be re-verified under the successor contract.
8. After each validated terminal event, apply only the global stop policy. If it
   is false, call `pi_search_pool_continue` for that exact `candidate_id` with a
   budget that fits remaining time. The runtime supplies this fixed neutral
   continuation prompt:

   ```text
   Continue the same autonomous search loop from the latest committed evidence.
   Refresh runtime context, choose the next evidence-backed hypothesis yourself,
   verify every material change, and keep working while the assigned budget remains.
   ```

   Pi continuation is cross-process native-session continuation: the supervisor
   launches a fresh Pi process in the same workspace, but it calls
   `search_continue_agent_session` and reloads the same persisted Pi session.
   It preserves both `agent_session_id` and candidate identity. Do not call
   `search_plan_next`, `search_start_batch`, or any new-candidate submission
   after initial pool creation. Do not vary continuation
   based on rank or improvement.
   `pi_search_pool_snapshot(run_id=...)` rediscovers the pool after an
   interrupted main Pi turn; use `pool_id` for later exact snapshots.
9. Call `pi_search_pool_close(mode="drain")` before selection, or
   `mode="interrupt"` when remaining work should be stopped. Then call
   `search_select` and `search_promote` when promotion is requested.
   `search_select` ranks verifier-recorded iterations, checks out the best
   committed candidate `git_head`, and runs a main-agent final verifier on that
   exact commit before recording the selected candidate.
10. Call `goal_plus_record_search_result`. Do not call `search_report` yet;
    result recording reserves the canonical report paths without generating
    Markdown or HTML.
11. Run the raw-goal audit. Keep the same run while its evaluation/edit contract
    remains adequate. A new incumbent or low-performing route never opens a
    replacement run. Create a successor only for a concrete spec/contract
    revision or distinct measurable subproblem, using `source_run_id`; inherited
    scores remain historical until reverified.
12. Run the final raw-goal audit. For a normal record, finish with
    `goal_plus_set_status`. If `policy.final_check.mode="required"`, instead:
    - call `goal_plus_prepare_final_check(checker_host="pi")`
    - pass the exact returned `launch` object to
      `pi_goal_plus_run_final_check`
    - let the stateless, read-only Pi reviewer call
      `goal_plus_submit_final_check` itself
    - address failed findings and prepare a fresh check
    A passing required check atomically marks the record complete.
13. Only after the Goal Plus record reaches a terminal status (`complete`,
    `blocked`, or `abandoned`), call `search_report` exactly once for every
    successfully recorded `run_id`. Never generate an intermediate Goal Plus
    report. Return the final Markdown and HTML paths to the user.

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

Use the existing raw-goal audit to consider the appropriate response. The
default after a score improvement is to continue the current run; changing
search direction, transferring a feature, or deepening an artifact does not
require a new frozen spec.

- `upgrade_spec`: concrete evidence shows that the current verifier or edit
  contract misrepresents the raw goal, or the measurable subproblem itself must
  change. Save a stronger draft, freeze a new spec, and create a new Search run.
  Never modify the prior frozen artifacts in place. Sparse diagnostics, low
  scores, slow progress, or a better search idea are not sufficient evidence.
- `keep_spec_with_justification`: the current spec remains a credible proxy for
  the raw goal. State the evidence for keeping it and direct subsequent Search
  remains available to every candidate loop; each subagent decides whether to
  pursue deeper or structurally different approaches.
- `revise_goal`: the effective scope, deliverables, or success criteria need to
  change. Call `goal_plus_update_goal` with the complete revised raw goal and
  current `expected_revision`, re-triage, then discover and freeze the spec for
  that revision.

These labels describe a main-agent decision inside the existing raw-goal audit;
they are not new runtime states, an additional workflow phase, or a user
approval checkpoint.

Every asynchronous completion may add a `verifier_assessment`. The main agent
must review reported `concern` evidence promptly because one worker can discover
an evaluation-contract defect while other workers remain live. Pause continuation
while checking, but do not interrupt siblings on an unconfirmed worker opinion.
Once confirmed, `search_invalidate_run` fences new plans, sessions, verifier
records, selection, and promotion; then the main agent must interrupt and wait
for every host worker before changing verifier files. An infrastructure failure
follows the same mandatory path. A quality or coverage concern requires
demonstrated ranking unreliability or missing raw-goal coverage; when a
standardized evaluator agrees with the target judge, retain it and continue the
same run.

One Goal Plus record is the complete user task. `search_tasks` is its
append-only search-task history; each item is one `run_id` over one frozen
spec. `linked_search` is only the current-task compatibility view. A search
task has one initial planning round in `parallel_loops` mode and may contain
many same-candidate worker sessions and verifier iterations.

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
disconnect. Each pool job internally performs the same chain:
`search_start_agent_session`, foreground Pi RPC worker launch,
`search_bind_agent_handle`, and the final `search_run_verifier` without
`agent_session_id` when `final_verify=true`. These mechanical steps are not
public Pi main-agent tools.

Do not call `search_start_agent_session`, `search_bind_agent_handle`, or
`search_continue_agent_session` from the Pi main agent; the managed pool owns
those mechanical steps. For another attempt on an existing candidate, call
`pi_search_pool_continue`. The supervisor then uses
`search_continue_agent_session` internally and records that step before
launching the next process against the same native session.

Initial pool launch and continuation are non-blocking; the detached wrapper owns the foreground Pi
RPC child and its cleanup. `worker_budget.max_runtime_seconds` is required and
maps to the Pi RPC process watchdog. `worker_budget.max_turns` is only a prompt
hint. There is no public synchronous candidate/batch runner or manual pool
submit API.

Pi workers persist native session JSONL under `.gp/host-sessions/pi/`. If a
worker times out, fails, or simply completes while the global stop policy is
false, call `pi_search_pool_continue`. The driver starts another Pi process,
reloads the same native session, preserves `agent_session_id`, and requests only
entries after the last persisted metrics cursor. MCP history, verifier
iterations, Git state, and bounded handoff remain authoritative recovery
evidence if native session loading fails.

Each continuation launch starts a new dispatch-scoped budget. A deadline,
closeout, or time advisory persisted in the native conversation from an earlier
dispatch is historical; only warnings delivered after the latest launch apply.

Exception: never redispatch after `failure_class=VerifierWorkspaceSideEffect`,
`metrics.infrastructure_failure=true`, or
`metrics.candidate_action=stop_and_report`. The worker must stop without
cleaning or retrying, and the main agent must not resume candidates on the
same `frozen_spec_id`. Repair the source-owned verifier, freeze a new spec, and
create a new run. The host driver, not the MCP runtime, owns closing out any
siblings that are still executing in the pool.

Each worker also leaves a bounded `progress_handoff` in its bound handle. It
combines the optional `.tmp/handoff.json` recovery note with a runner-owned Git
and verifier snapshot. `search_get_agent_context` exposes it under
`context.resume`; use this explicit resume object for artifact and verifier
facts. Native Pi conversation may preserve reasoning and continuation
instructions, but it must never override durable evidence or top-N history.

When a candidate continues after its first dispatch, pass an explicit
`worker_budget` to `pi_search_pool_continue`. This creates a fresh Pi process
for the same native session and candidate workspace with a dispatch budget
chosen from the outer remaining time; it does not mutate the frozen spec.

Do not redispatch only because the worker handle has `timed_out=true`. When the
candidate already has a `process_passed=true` Git-backed iteration, that best
iteration remains valid search evidence and eligible for later planning and
selection. Official history reports that best evidence in `score` and
`best_iteration`, while `latest_score` and `latest_process_passed` preserve a
later timeout or regression for diagnosis.

History is runtime-owned, not a local plan file. Workers must call
`search_get_agent_context` first and use `context.resume`, `context.history`,
`context.iterations`, `context.results`, and the inherited
`context.results_tsv` as the resume source. Every verifier call supplies a
concise `hypothesis`; the runtime validates the inherited workspace-root
`results.tsv`, appends exactly one row for every returned report, and commits
the ledger. Workers never edit it directly. Later-iteration history includes the
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

Before Search Mode tool use and main-agent mutating tools (`bash`, `edit`, and
`write`), Pi's
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

For one worker, use
`search_get_agent_observability(agent_session_id)`. It returns the same
versioned model/timing/terminal/usage/context/artifact/handoff schema across
hosts and never returns prompt, reasoning, or tool payload bodies.

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
