---
name: search
description: Internal Search Mode engine for /goal-plus with the goal-plus MCP server from Codex.
---

# Search Mode Runtime for Codex

Use this skill after `/goal-plus` has upgraded a goal to Search Mode, or for
explicit low-level debugging of an already measurable SearchSpec. The normal
user-facing entrypoint is `/goal-plus`.

Use the logical `search_*` tools exposed by the `goal-plus` MCP server. Codex
may display MCP tools with a client-specific prefix; match by the final logical
tool name.

## Verifier Freeze Contract

Before `search_freeze_spec`, run the proposed `ranking_signal` from
`source_path` and confirm its final non-empty stdout line is JSON with a finite
numeric `spec.metric_name`, for example `{"combined_score": 123.0}`. The
command may be inline or call an existing repository tool. Create a custom
verifier file only when needed and materialize it during Spec Discovery before
freezing, in a source-owned path such as `.goal-plus-verifiers/`, never `.gp/`
or `.search/`. The freeze tool exposes the complete nested `SearchSpec` schema.
`expected_outputs` accepts artifact path/glob strings only and does not parse
stdout.

The freeze preflight runs in a disposable source copy and treats the candidate
workspace as read-only. Verifiers must put compiler products and temporary
outputs in `GOAL_PLUS_VERIFIER_TMPDIR`/`TMPDIR` or a
`tempfile.TemporaryDirectory()`. Never use one fixed `/tmp` pathname: parallel
candidate verification would collide. Any workspace change raises
`VerifierWorkspaceSideEffect`; repair the verifier and freeze a new spec before
starting candidates.

If runtime verification returns `VerifierWorkspaceSideEffect`,
`metrics.infrastructure_failure=true`, or
`metrics.candidate_action=stop_and_report`, the affected worker must stop
immediately. It must not delete generated files, modify frozen verifier assets,
reset around the failure, or retry. The parent invalidates the run, quiesces
every live worker, repairs the source-owned verifier, freezes a new spec, and
creates a successor run. Never select or promote an invalidated run.
Use `search_invalidate_run` before `interrupt_agent` so in-flight verifier
results cannot enter the old run.
Create that successor with `source_run_id` so durable research lineage is
preserved while every inherited artifact is reverified.

## Parallel Loop Contract

New Codex Search runs use:

```yaml
strategy:
  orchestration_mode: parallel_loops
  worker_host: codex
```

`parallel_loops` means:

- create the initial candidate set once;
- one candidate workspace is one autonomous search loop;
- the candidate subagent owns all later hypothesis, pivot, rebase, and
  AutoResearch decisions inside that workspace;
- a terminal worker event causes validation and same-worker continuation, not
  another planning round;
- normal execution never creates a replacement candidate or refills a slot;
- runtime rejects a second `search_plan_next` call for the run;
- `search_select` and `search_promote` remain parent-owned final Search actions;
  for Goal Plus, `search_report` is deferred until the parent Goal Plus record
  is terminal.

The parent is a completion validator and continuation trigger, not a search
conductor. A low score, one non-improving turn, or another candidate leading is
not a reason to stop or replace a worker.

## Search Run Budget Planning

Choose the whole-run budget before `search_freeze_spec`; it is frozen and
cannot grow inside that run.

1. Reserve time for final drain, selection, parent verification, reporting,
   and promotion.
2. Choose the number of initial autonomous loops with `max_parallel`. When no
   better resource signal exists, recommend 4.
3. In the normal no-replacement flow, set `max_candidates` equal to
   `max_parallel`. Extra candidate budget is not permission to create later
   rounds in `parallel_loops` mode.
4. Give each initial worker enough uninterrupted runtime to create a real
   artifact and verifier evidence.
5. Use only global stop facts to decide whether to resume: explicit target
   reached, user stop, invalidated run, or insufficient outer time for another
   worker turn plus final closeout.

Different candidate ids do not by themselves provide search diversity. Use
distinct initial proposals or seeds when useful, then let each subagent choose
its own evidence-backed next hypothesis. After substantial nearby attempts
without progress, the subagent, not the parent, reassesses theoretical or
structural limits and pivots within its candidate loop.

## Main Workflow

1. Call `search_freeze_spec` for the Goal Plus spec draft, or `search_create`
   when a suitable frozen spec already exists. New specs must set
   `strategy.orchestration_mode="parallel_loops"` and `worker_host="codex"`.
2. Call `search_plan_next(requested_k=budget.max_parallel)` exactly once, then
   call `search_start_batch` exactly once to create the initial candidates.
3. For every candidate, call `search_start_agent_session`. Its optional
   `worker_budget` is a one-dispatch host limit; it does not change the
   candidate's technical direction.
4. Launch a Codex subagent with the returned launch payload:
   - project it onto the current `spawn_agent` tool schema;
   - always pass `task_name`, `message`, and `fork_turns` when exposed;
   - pass `agent_type`, `model`, `reasoning_effort`, or `service_tier` only when
     both the launch payload and current tool schema expose that field;
   - Never synthesize optional launch metadata;
   - when no model override can be passed, the worker inherits the parent Codex
     model; in other words, it inherits the parent Codex model.
5. Bind the returned task name/nickname with `search_bind_agent_handle` using
   `host="codex"`.
6. Track every live worker and its own watchdog deadline. Use targetless
   `wait_agent`, then `list_agents`, and process every worker that is terminal.
   A progress-only wakeup is not a completion event.
7. For each terminal worker:
   - read the current run best from `search_list_history` or
     `goal_plus_monitor_snapshot`;
   - bind the terminal handle and summary with `search_bind_agent_handle`; the
     terminal bind automatically harvests bounded `.tmp/handoff.json` data;
   - use `search_get_agent_observability(agent_session_id)` when native model,
     token, duration, context, or terminal evidence is needed; this is read-only
     and does not replace `list_agents` for liveness;
   - call `search_run_verifier(hypothesis="main completion verification")`
     without `agent_session_id`;
   - refresh history/monitor and record whether the verifier-backed global best
     candidate/score changed;
   - inspect `verifier_assessment` only for a concrete evaluation-contract or
     infrastructure failure. Sparse diagnostics, low score, or no improvement
     are not verifier inadequacy and do not block continuation.
8. Apply the global stop policy after validation:
   - stop resuming when an explicit success criterion is satisfied;
   - stop when the run is invalidated or the user stopped it;
   - stop when remaining outer time cannot fit another worker turn plus final
     closeout;
   - otherwise resume the same native Codex subagent on the same candidate.
9. To resume, first call `search_continue_agent_session(agent_session_id)` with
   no new technical directive, then project the returned continuation payload
   onto `followup_task` for the existing task. Use this exact neutral message:

   ```text
   Continue the same autonomous search loop from the latest committed evidence.
   Refresh runtime context, choose the next evidence-backed hypothesis yourself,
   verify every material change, and keep working while the assigned budget remains.
   ```

   Do not mention a parent-preferred direction, feature transfer, macro restart,
   or ranking-based judgment. The resumed worker owns that decision.
10. Do not call `search_plan_next` or `search_start_batch` after the initial
    candidates exist. Do not leave a slot idle because its candidate is weak;
    either resume the same worker under the global stop policy or stop the whole
    search for a global reason.
11. Before each wait, compare the nearest worker deadline with the current
    time. At soft closeout use `send_message` exactly once with the configured
    closeout message; at the hard
    deadline call `interrupt_agent`, observe the terminal state, and validate
    it like any other completion. One worker timeout does not stop siblings.
12. When the global stop policy is true, drain or interrupt every live worker,
    then call `search_select` and `search_promote` when requested. Selection
    uses verifier-backed Git iterations; do not promote merely to checkpoint a
    temporary best. When this Search belongs to Goal Plus, return control
    without calling `search_report`; the Goal Plus skill generates it exactly
    once after the parent record is terminal. For standalone Search, call
    `search_report` only after promotion.

## Best-So-Far Contract

`search_run_verifier` is metric-direction aware and updates the durable
`run.best_score` and `run.best_candidate_id` when a passing result is better.
The parent must observe this after every completion verification, but it must
not turn the comparison into a continuation decision.

- Better result: keep it as the latest verifier-backed answer, then resume the
  same worker if the global stop policy is false.
- Worse or equal result: preserve the earlier best, then still resume the same
  worker if the global stop policy is false.
- Final selection: run only after all workers have drained.

## Worker Budget Control

`budget_control.mode == "parent_watchdog"` means the parent Codex agent enforces
elapsed worker time. `spawn_agent` has no timeout argument, so combine
`wait_agent` with closeout and `interrupt_agent`.

`worker_budget.max_runtime_seconds` is the enforceable upper bound for one
dispatch. A continuation may receive another one-dispatch host limit derived
from the remaining outer time.

Treat `budget_control.max_turns_hint` as prompt guidance only. The hard limit is
the sum of `initial_wait_timeout_ms` and `final_wait_timeout_ms`, followed by
interruption. `soft_closeout_seconds` is the closeout window. Send the configured
`closeout_message` exactly once for that dispatch. Continuation resets only the
same worker's dispatch deadline.

When `worker_budget.min_runtime_seconds` or `min_verifier_runs` is present,
`budget_control.autoresearch_lease.mode == "subagent_stop"` is a lower-bound
lease enforced by the candidate's `SubagentStop` hook. An early final response
is blocked and the continuation returns to the same Codex worker without
returning control to main. Never send the parent closeout message while this lease is active.
Do not poll or sleep; continue hypothesis -> artifact ->
verifier cycles. Infrastructure `stop_and_report` evidence bypasses the lease.

Project `PostToolUse` hooks may also provide an advisory-only timing message to
the bound candidate worker. It may use `GOAL_PLUS_OUTER_DEADLINE_AT` when
available. It never stops the worker and must not trigger for the main agent,
ordinary subagents, or final checker.

Main agent, ordinary subagent, and final-checker PostTool events must not
trigger this candidate advisory.

Continuation budgets are derived from outer remaining time and final closeout
reserve, not from whether the parent likes the candidate. Do not give a weak
candidate an intentionally unusable budget or a strong candidate a new
technical directive.

## Runtime History And State-level Resume

History is runtime-owned, not a `plan.md` file. Workers recover through
`search_get_agent_context`, including `context.history`, `context.iterations`,
`context.results`, `context.results_tsv`, workspace Git state, and bounded
handoff metadata. Host transcript is useful context but is not authoritative
Search state.

Codex same-worker continuation uses `search_continue_agent_session` followed by
`followup_task` on the existing task. The worker must refresh context at the
start of every resumed turn.

Use `search_redispatch_candidate` only when the original native worker cannot be
continued. Redispatch must keep the same candidate/workspace. It is recovery,
not a normal way to introduce another search direction.

## Contamination Recovery

Normal low performance never creates a new candidate. A replacement/fork is
allowed only after objective recovery evidence such as unrecoverable Git or
ledger corruption, an unusable native session plus failed same-candidate
redispatch, or a verifier/spec invalidation that requires a successor run.

Recovery order:

1. same native worker continuation;
2. same-candidate state redispatch;
3. restore the same candidate from its latest verifier-backed Git revision;
4. only through an explicit future recovery contract, fork a replacement with
   durable lineage.

Do not use ordinary `search_plan_next` as that recovery contract in
`parallel_loops` mode.
