---
name: search
description: Internal Search Mode engine for /goal-plus with foreground Claude Code agents and the goal-plus MCP server.
---

# Search Mode Runtime for Claude Code

Use this skill after `/goal-plus` has upgraded a goal to Search Mode, or for
explicit low-level debugging of an already measurable SearchSpec. The normal
user-facing entrypoint is `/goal-plus`.

Use the logical `search_*` tools exposed by the `goal-plus` MCP server.
Claude Code may display MCP tools with a server prefix; match by the final
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
3. Estimate one batch duration from the selected worker tier, prior observed
   worker durations, and launch/verifier overhead. Under real concurrency use
   the slowest worker duration, not the sum of all workers.
4. Estimate `rounds = floor((remaining_seconds - final_reserve_seconds) /
   estimated_batch_seconds)`, subject to explicit attempt/token caps, then set
   `max_candidates = rounds * max_parallel`. Keep at least one candidate only
   when enough time remains to produce and verify useful work.

For example, 7200 seconds remaining, 900 seconds reserved, and 1260 seconds per
batch gives 5 rounds; with `max_parallel=3`, set `max_candidates=15`.

After every completed batch, refresh remaining time and history before calling
`search_plan_next` again. `requested_k` is only the request for that round; use
at most `max_parallel` and the remaining total candidate budget. Do not treat
its default value 4 as the whole-run budget. Do not call `search_select` while
another useful batch fits the remaining budget. Select only when the candidate
cap is exhausted, another batch no longer fits before the final reserve, an
explicit attempt/token cap is reached, or a declared early-stop condition holds.

Before starting another candidate, assess whether recent attempts cluster
around the same underlying mechanism or bottleneck. Different candidate ids do
not by themselves provide search diversity. When work has concentrated in one
family, step back and analyze the current bottleneck, then prefer a materially
different high-potential direction when the evidence supports one. This is
advisory: it does not require `macro_restart` or impose an action quota.

After substantial attempts without meaningful progress, do not keep applying
nearby mutations by default. Reassess the objective's applicable theoretical or
structural limits, such as lower or upper bounds, critical paths, resource
bottlenecks, saturation evidence, or infeasibility constraints. Use that
analysis to identify a credible breakthrough and decide whether to deepen or
redirect; the analysis does not force any particular action.

When an existing candidate remains promising and further progress benefits from
its accumulated workspace understanding, prefer same-candidate continuation or
state-level redispatch with a larger worker tier over launching near-duplicate
candidates. Parallel candidates in the same feature family are useful only when
they test materially distinct hypotheses. Available candidate capacity is not
an obligation to launch more work.

## Main Workflow

1. Call `search_freeze_spec` for the Goal Plus spec draft, or `search_create`
   when a frozen spec already exists.
2. Call `search_plan_next`.
3. Call `search_start_batch`.
4. For each new candidate, call `search_start_agent_session`.
5. Launch a foreground Agent using the returned launch payload:
   - agent type: `launch.agent_type`
   - message: `launch.message`
   - background: false
6. Keep workers in the foreground. If `launch.budget_control.mode == "host_turn_limit"`,
   verify that `launch.agent_type` names a Claude agent definition with a matching
   `maxTurns` frontmatter value before launch.
7. If the Agent result includes an agent id or reusable agent name, call `search_bind_agent_handle` with:
   - `host: "claude-code"`
   - `external_id`
   - `task_name` only when the client exposes a stable name instead of an id
8. If a worker reaches `maxTurns` before useful verifier evidence, call
   `search_redispatch_candidate(run_id, candidate_id, directive?,
   worker_agent_type="search-candidate-agent-deep",
   worker_budget={"max_turns": 16, "on_exceed": "interrupt"})` and launch the
   returned foreground Agent payload for the same candidate workspace.
9. Run final `search_run_verifier(hypothesis="main final verification")` from
   the main agent before selecting. Every returned report appends exactly one
   validated row to the runtime-owned inherited workspace-root `results.tsv`
   and commits it.
10. Use `search_select` and `search_promote` when appropriate. For Goal Plus,
    return without reporting; its parent skill calls `search_report` exactly
    once after the Goal Plus record is terminal. Standalone Search calls
    `search_report` only after promotion.

## Worker Budget Control

Claude Code worker runtime is controlled through foreground agent definitions.
Use `launch.agent_type` exactly as returned by the runtime:

- `search-candidate-agent-flash` has `maxTurns: 4`
- `search-candidate-agent` has `maxTurns: 8`
- `search-candidate-agent-deep` has `maxTurns: 16`

`budget_control.max_turns` documents the expected bound. The enforcement comes
from the selected Claude Code agent's `maxTurns` frontmatter. The runtime maps
known budgets 4, 8, and 16 to the matching agent types when `worker_agent_type`
is omitted.

Choose the initial tier before freezing the spec:

- Use `search-candidate-agent-flash` only for smoke tests or very cheap probes.
- Use `search-candidate-agent` for normal candidate work.
- Use `search-candidate-agent-deep` when the source tree is large, the verifier is
  slow, the edit requires cross-file reasoning, or a previous flash worker
  reached `maxTurns` before recording any verifier iteration or usable score.

If a worker returns no useful verifier evidence because the tier was too small,
prefer a higher tier for later planned work or a replacement search run. Do not
repeat the same underpowered tier unless the user explicitly wants a cheap
probe.

## Runtime History And Resume

History is runtime-owned, not a `plan.md` file. The main agent reads prior
candidate results through `search_list_history`; workers recover state through
`search_get_agent_context`, which returns `context.history` and
`context.iterations`.

For hosts or tool surfaces that cannot re-enter the same foreground agent, use
state-level resume: call `search_redispatch_candidate` to start a new
foreground Agent for the same candidate workspace, optionally overriding
`worker_agent_type` and `worker_budget.max_turns`. The returned prompt tells
the worker to treat `search_get_agent_context` as the authoritative resume
context. Do not ask the worker to infer prior attempts from chat transcript.

## Continuation

If `search_continue_agent_session` returns a `SendMessage` payload and the
current Claude Code tool surface actually exposes a usable `SendMessage` tool,
send the message to the specified agent in the foreground. If no handle is
bound, `SendMessage` is unavailable, or the host cannot prove same-agent
continuation, call `search_redispatch_candidate` and rely on MCP
history/iterations for resume.
