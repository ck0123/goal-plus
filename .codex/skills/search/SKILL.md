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

The freeze preflight runs in a disposable source copy and treats the candidate
workspace as read-only. Verifiers must put compiler products and temporary
outputs in `GOAL_PLUS_VERIFIER_TMPDIR`/`TMPDIR` or a
`tempfile.TemporaryDirectory()`. Never use one fixed `/tmp` pathname: a Search
batch may verify several isolated candidates concurrently. Any workspace change
raises `VerifierWorkspaceSideEffect`; repair the verifier and freeze a new spec
before starting candidates.

If runtime verification still returns `VerifierWorkspaceSideEffect`,
`metrics.infrastructure_failure=true`, or
`metrics.candidate_action=stop_and_report`, the worker must stop immediately.
It must not delete generated verifier files, edit frozen assets, reset around the
failure, or retry. The parent must not redispatch that candidate or spend another
batch on the same `frozen_spec_id`; repair the source-owned verifier, freeze a
new spec, and create a new run. In a concurrent batch, host lifecycle controls
remain responsible for closing out siblings that have not already returned.

## Search Run Budget Planning

Choose the whole-run candidate budget before `search_freeze_spec`; it is frozen
and cannot grow inside that run. `budget.max_candidates` is the total number of
distinct candidate workspaces. `budget.max_parallel` is the hard cap on live
candidate workers. A persisted Search round is a planning decision epoch, not a
worker barrier: the next plan may be created as soon as any worker completes and
a slot is free.

When the user or outer harness supplies a wall-clock, attempt, or token budget:

1. Reserve time for main-agent final verification, selection, reporting, and
   promotion.
2. Choose `max_parallel` that the host can support. When no better
   resource signal exists, recommend 4; this is a planning recommendation, not
   a runtime default.
3. Set `max_candidates` as a conservative whole-run safety cap that fits the
   outer budget. Do not turn it into a required round count or promise that all
   candidate slots will be consumed.
4. Give initial probes enough uninterrupted time to produce real artifacts and
   verifier evidence. Reinvest more time in valuable directions and stop weak
   directions based on evidence and remaining time.

After every terminal worker event, refresh remaining time and
`search_list_history`. Review the current-run `feature_ledger`,
`verifier_assessments`, and pitfalls. Treat pitfalls as conditional evidence:
`candidate_local` stays with one candidate, `feature_family` transfers only
when mechanism and conditions match, and `evaluation_contract` changes run
policy only after main-agent confirmation. A single observation never forbids
another candidate. Consider all three search actions without imposing a quota:
`deepen_incumbent`, `transfer_feature` from any candidate
(including one outside the visible ranking frontier), and `macro_restart` from
source or an earlier ancestor. Decide independently whether each free slot
should continue a promising worker, start a candidate for one of those actions,
remain idle, or begin final selection. Record the chosen action in
`proposal.metadata.search_action`. `requested_k` is only the number of new
candidate workspaces desired at that decision point. Never wait for unrelated
slow workers merely to preserve a batch boundary.

Before starting another candidate, assess whether recent and active attempts
cluster around the same underlying mechanism or bottleneck. Different candidate
ids do not by themselves provide search diversity. When work has concentrated
in one family, step back and analyze the current bottleneck, then prefer a
materially different high-potential direction when the evidence supports one.
This is advisory: it does not require `macro_restart`, impose an action quota,
or make superficial difference more valuable than a strong hypothesis.

After substantial attempts without meaningful progress, do not keep applying
nearby mutations by default. Reassess the objective's applicable theoretical or
structural limits, such as lower or upper bounds, critical paths, resource
bottlenecks, saturation evidence, or infeasibility constraints. Use that
analysis to identify a credible breakthrough and decide whether to deepen,
transfer, or redirect; the analysis does not force any particular action.

When an existing candidate remains promising and further progress benefits from
its accumulated source and workspace understanding, prefer same-candidate
continuation with a larger one-dispatch budget over launching near-duplicate
candidates. Parallel candidates in the same feature family are useful only when
they test materially distinct hypotheses. A free slot is not an obligation to
launch more work.

## Main Workflow

1. Call `search_freeze_spec` for the Goal Plus spec draft, or `search_create`
   when a frozen spec already exists.
2. Fill up to `budget.max_parallel` initial slots with `search_plan_next` and
   `search_start_batch`.
3. For each new candidate, call `search_start_agent_session`. Its optional
   `worker_budget` is a one-dispatch override for a direction that deserves a
   longer uninterrupted initial exploration; it does not mutate the frozen
   spec.
4. Launch a Codex subagent with the returned launch payload:
   - Project the payload onto the current `spawn_agent` tool schema. Always pass
     `task_name`, `message`, and `fork_turns` when those fields are exposed.
   - Pass optional `agent_type`, `model`, `reasoning_effort`, or `service_tier`
     metadata only when the current tool schema exposes the corresponding
     field. Some Codex configurations intentionally hide this metadata.
   - Do not fail merely because optional launch metadata is hidden. When no
     model override can be passed, the worker inherits the parent Codex model.
5. If `spawn_agent` returns a task name or nickname, call `search_bind_agent_handle` with:
   - `host: "codex"`
   - `task_name`
   - `nickname` when present
6. Track every live worker and its own absolute watchdog deadline. Call
   targetless `wait_agent` so the parent wakes when any worker produces a
   mailbox update; then call `list_agents` and process every worker that is now
   terminal. A progress-only wakeup is not a completion event, so keep waiting
   when no worker is terminal.
7. For each terminal worker, bind its final summary/timeout metadata and run
   `search_run_verifier(hypothesis="main final verification")` from the main
   agent. Every returned report appends exactly one validated row to the
   runtime-owned inherited workspace-root `results.tsv` and commits it. Only
   after that verifier returns is
   the pool event `candidate_ready`. Refresh history immediately; do not wait
   for the other live workers. `candidate_ready` is a decision event, not run
   completion. Inspect its `verifier_assessment`: sparse diagnostics, a low
   score, or lack of progress are not grounds to refreeze, while demonstrated
   evaluation-contract misalignment must be investigated before spending more
   candidate budget. Pause refill while investigating. If the concern is not
   confirmed, record why the spec remains adequate and resume. If the main
   agent confirms contract, coverage, determinism, target-alignment, or
   infrastructure failure, call `search_invalidate_run` first so no later
   verifier result can enter the run; then `interrupt_agent` every live worker
   and use `list_agents`/`wait_agent` until all are terminal. Preserve their
   handoffs, repair or regenerate the verifier only after quiescence, freeze a
   new spec, and call
   `search_create(new_frozen_spec_id, source_run_id=old_run_id)`. Never
   select/promote the invalidated run. Inherited features must be re-verified;
   inherited scores are historical only.
8. Unless verifier investigation has paused refill, choose one action for each
   newly free slot:
   - call `search_continue_agent_session(..., worker_budget?)`, then project its
     launch payload onto `followup_task`, to give the same Codex worker a deeper
     turn on the same candidate;
   - call `search_plan_next(requested_k=<new direction count>)`,
     `search_start_batch`, and launch a new candidate;
   - leave the slot idle because no useful work fits the remaining time; or
   - begin final drain and selection.
9. If a worker stops before useful verifier evidence and native continuation is
   no longer available, call
   `search_redispatch_candidate(run_id, candidate_id, directive?,
   worker_agent_type?, worker_budget={"max_runtime_seconds": <larger seconds>, ...})`
   and launch the returned payload as a new worker for the same
   candidate.
10. Before each wait, compare the nearest live-worker deadline with the current
    time. At soft closeout send exactly one `send_message`; at the hard deadline
    call `interrupt_agent`, observe the terminal state, and process it like any
    other completion. Do not apply one worker's timeout to the whole pool.
11. Drain or interrupt every live worker before `search_select`. Then use
    `search_select`, `search_report`, and `search_promote` when appropriate.
    Do not select/promote merely to checkpoint a new incumbent;
    verifier-recorded Git iterations already preserve it. Keep the same run
    while the evaluation/edit contract is adequate and candidate budget remains.
    If a new run is unavoidable because the contract/subproblem changed or the
    immutable run budget is exhausted, read the old run history and pass
    `source_run_id` to `search_create`. The successor's `inherited_research`
    explicitly snapshots the old frontier, scoped pitfalls, feature ledger, and
    non-winning portable innovations. It never imports old scores as current
    evidence.

## Worker Budget Control

`budget_control.mode == "parent_watchdog"` means the runtime expects the parent
Codex agent to enforce elapsed worker time. Codex `spawn_agent` does not accept
a timeout argument, so the parent must combine `wait_agent` with an interrupt.

Treat `budget_control.max_turns_hint` as a prompt-level hint only. The hard
control for Codex is the sum of `budget_control.initial_wait_timeout_ms` and
`budget_control.final_wait_timeout_ms`, followed by interruption. The
`soft_closeout_seconds` field records the closeout window; it is not a
runtime-owned worker timer. Send the payload's configured `closeout_message`
exactly once for that worker; completion or continuation resets only that
worker's dispatch deadline, never the whole pool.

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
one-dispatch override on initial launch or redispatch. Treat the frozen budget
as a baseline, not a requirement to give every direction equal depth. When a
direction is promising, allocate a larger budget that fits the outer remaining
time and let the worker continue hypothesis -> artifact -> verifier cycles
while distinct evidence-backed hypotheses remain and the expected information
or performance gain justifies the time. You may also override
`worker_agent_type` when local Codex agent
variants exist, but that is prompt/agent selection, not a hard step cap. If a
watchdog stops a worker before it records any verifier iteration or usable
final score, do not repeat the same underpowered budget unless the user
explicitly wants a cheap probe.

## Runtime History And Resume

History is runtime-owned, not a `plan.md` file. The main agent reads prior
candidate results through `search_list_history`; workers recover state through
`search_get_agent_context`, which returns `context.history` and
`context.iterations`. When a bound worker provides `.tmp/handoff.json`, later
history also includes its structured `research_summary`. Use the verifier-backed
feature ledger in `key_results`, scoped conditional `pitfalls`, `blockers`,
`next_steps`, and `verifier_assessment` to design later candidate proposals; do
not carry only the best score or raw transcript text. Each feature records its
code surface, artifact/git head, portability, dependencies, measured effect,
and relation to the incumbent. The run-level ledger deliberately retains
features from candidates outside the visible score frontier.

When `inherited_research` is present, use it only to seed hypotheses and
feature-transfer probes. Candidate ids are qualified by `source_run_id`, and
source scores are non-reusable until the successor verifier records them again.

Codex supports same-worker continuation through `followup_task`. First call
`search_continue_agent_session` so the runtime records the directive and
one-dispatch budget, then invoke the returned follow-up payload. The worker must
still refresh `search_get_agent_context`; host transcript is useful context but
is not authoritative Search state. Use `search_redispatch_candidate` only when
the original worker cannot be continued or a fresh context is intentional.

## Continuation

Same-worker continuation uses `search_continue_agent_session` followed by
Codex `followup_task`. State-level resume remains available through
`search_redispatch_candidate`, which creates a new `agent_session_id` for the
same candidate workspace and relies on MCP history/iterations.
