---
name: search
description: >
  Run Agentic Search for measurable multi-candidate coding tasks.
  Use when the user asks to try several candidate fixes,
  optimizations, or configurations under a frozen verifier.
argument-hint: >
  Objective, source path, allowed files, verifier command/artifacts, budget.
---

# Agentic Search Skill

This skill is the internal Search Mode engine for `/goal-plus`. It runs isolated
candidate workspaces, frozen verifier execution, iteration scoring, and
OpenCode-native subagent lifecycle after Goal Plus has decided to upgrade a
goal. The MCP runtime is not a process supervisor — OpenCode owns the actual
`Task` lifecycle, step cap, and return value. The runtime owns specs, plans,
workspaces, verifier scoring, history, reports, and promotion patches.

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

## OpenCode Host Notes

Run OpenCode normally through `/goal-plus`:

```bash
opencode
opencode run --command goal-plus "<prompt>"
```

`opencode run --command search` is reserved for internal debugging of the
Search Mode engine and should not be the normal user entrypoint.

OpenCode `Task` has no `timeout` parameter. Subagents run until their OpenCode step cap (15/50/100/150 depending on `worker_agent_type`) hits or the user interrupts the run. There are no per-session or run-level time deadlines in this runtime. Do not pass or invent a Task-level timeout.

## Tool Names In OpenCode

The MCP server is configured as `goal-plus`, so tools appear with this prefix:

| Runtime tool | OpenCode tool name |
|---|---|
| `search_freeze_spec` | `goal-plus_search_freeze_spec` |
| `search_create` | `goal-plus_search_create` |
| `search_status` | `goal-plus_search_status` |
| `search_list_history` | `goal-plus_search_list_history` |
| `search_plan_next` | `goal-plus_search_plan_next` |
| `search_start_batch` | `goal-plus_search_start_batch` |
| `search_start_agent_session` | `goal-plus_search_start_agent_session` |
| `search_redispatch_candidate` | `goal-plus_search_redispatch_candidate` |
| `search_bind_opencode_session` | `goal-plus_search_bind_opencode_session` |
| `search_continue_agent_session` | `goal-plus_search_continue_agent_session` |
| `search_get_agent_context` | `goal-plus_search_get_agent_context` |
| `search_run_verifier` | `goal-plus_search_run_verifier` |
| `search_list_iterations` | `goal-plus_search_list_iterations` |
| `search_select` | `goal-plus_search_select` |
| `search_report` | `goal-plus_search_report` |
| `search_promote` | `goal-plus_search_promote` |

If any of these tools are unavailable, stop and report that the MCP server is not connected. Do not simulate runtime state in chat.

There are no MCP `wait`, `abort`, `finish`, `submit`, observation, status, or host-sync tools. Stopping a running subagent is an OpenCode/user interruption concern, not an MCP call.

## Required Discipline

1. Do not start candidate execution before freezing the SearchSpec and verifier artifacts.
2. Do not modify verifier files during candidate execution.
3. Do not edit the main source workspace while exploring candidates.
4. Subagents self-verify via `search_run_verifier` with their own `agent_session_id`. After OpenCode Task returns, call `search_run_verifier` yourself (without `agent_session_id`) to confirm the final score against the best-so-far workspace state.
5. Do not promote by manually copying files. Use `search_promote`; it exports a patch/report.
6. If a candidate touches denied files or files outside the edit surface, run verifier anyway and let the runtime mark it failed.

## SearchSpec

Minimum shape:

```json
{
  "objective": "measurable task objective",
  "metric_name": "primary_metric",
  "metric_direction": "maximize",
  "source_path": "path/to/project",
  "edit_surface": {
    "allow": ["files/or/globs/the/candidate/may/edit"],
    "deny": ["verifier/or/config/files"]
  },
  "process_verifiers": [
    {
      "name": "ranking_signal",
      "role": "ranking_signal",
      "command": ["command", "arg"],
      "timeout_seconds": 30
    }
  ],
  "promotion_verifiers": [
    {
      "name": "anti_cheat_gate",
      "role": "anti_cheat_gate",
      "command": ["goal-plus-internal", "check-frozen-hashes"]
    }
  ],
  "budget": {
    "max_candidates": 4,
    "max_parallel": 2
  },
  "strategy": {
    "name": "agent_guided",
    "driver": "builtin",
    "worker_mode": "agent-session-pool",
    "worker_agent_type": "SearchCandidateAgent",
    "history_policy": {
      "scope": "top_n",
      "top_n": 5
    }
  }
}
```

`max_candidates` is the immutable total candidate-workspace cap across the
entire run and all planning rounds. `max_parallel` is the maximum width of one
planned batch — the runtime does not gate session creation on it and does not
supervise Task lifecycle. The planned round capacity is approximately
`ceil(max_candidates / max_parallel)`; equal values normally permit only one
full batch. There are no runtime-owned time budgets. Subagents run until their
OpenCode step cap hits or the user interrupts. Users can interrupt anytime and
query current best via `search_list_history` / `search_status`.

### Search Run Budget Planning

Choose the whole-run candidate budget before `search_freeze_spec`; it cannot
grow after freeze. When the user or an outer harness supplies a wall-clock,
attempt, or token budget:

1. Reserve time for main-agent final verification, selection, reporting, and
   promotion.
2. Choose a batch width `max_parallel` that the host can support. When no better
   resource signal exists, recommend 4; this is a planning recommendation, not
   a runtime default.
3. Estimate one batch duration from the selected OpenCode worker tier, prior
   observed worker durations, and launch/verifier overhead. If workers actually
   run concurrently, use the slowest worker duration rather than their sum.
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

`strategy.worker_mode` must be `agent-session-pool` (the only supported value). Retired values are rejected at parse time — fix the spec instead of relying on normalization.

`strategy.worker_agent_type` selects the default OpenCode subagent variant, which fixes the per-session step cap:

| Variant | Steps | Use when |
|---|---|---|
| `SearchCandidateAgentFlash` | 15 | Smoke tests, cheap iterations |
| `SearchCandidateAgent` (default) | 50 | Standard autoresearch loop |
| `SearchCandidateAgentDeep` | 100 | Sustained iteration on harder problems |
| `SearchCandidateAgentExtraDeep` | 150 | Extensive search, complex fixtures |

Custom Python strategies may return a plan-level `worker_policy` that overrides the default worker tier for the next candidate batch. Always use `session.launch.subagent_type` from `search_start_agent_session`; it is the authoritative Task tier after any strategy routing.

## Main-Agent Dispatch Policy

Choose the initial tier before freezing the spec, and raise the tier for later
work when the prior worker did not produce useful verifier evidence:

- Use `SearchCandidateAgentFlash` only for smoke tests, very cheap probes, or tasks
  where a partial answer is acceptable.
- Use `SearchCandidateAgent` for normal candidate work.
- Use `SearchCandidateAgentDeep` or `SearchCandidateAgentExtraDeep` when the source tree is
  large, the verifier is slow, the edit requires cross-file reasoning, or a
  previous flash/default worker returned without any `search_run_verifier`
  iteration or usable final score.

History is runtime-owned, not `plan.md`. The main agent reads prior candidate
results through `search_list_history`; workers read `context.history` and
`context.iterations` from `search_get_agent_context`. If a worker stops before a
useful result, call `search_redispatch_candidate` for the same candidate with a
higher `worker_agent_type` instead of repeating the underpowered launch. Do not
ask the worker to infer history from chat transcript.

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
its accumulated session and workspace understanding, prefer same-candidate
continuation or state-level redispatch with a larger worker tier over launching
near-duplicate candidates. Parallel candidates in the same feature family are
useful only when they test materially distinct hypotheses. Available candidate
capacity is not an obligation to launch more work.

## Workflow

### Step 1: Probe Read-Only Context

Read enough files to identify:

- objective and metric
- source path
- allowed edit files
- denied verifier/config files
- process verifier command
- promotion verifier command, if any
- budget: `max_candidates`, `max_parallel`

`spec.metric_name` becomes the column-2 header of every subagent's `results.tsv`, so pick a legible, domain-specific name (e.g. `val_bpb`, `pass@1`, `coverage`, `combined_score`). Avoid generic names like `score` — they defeat the purpose of a per-run metric column. `spec.metric_direction` (`minimize`/`maximize`) is what the subagent uses to decide whether an iteration improved.

For bundled examples, load the matching JSON file from `examples/`. If the user gives extra budget instructions, modify the spec object before freezing.

Treat "start by requesting N candidates" as `search_plan_next(..., requested_k=N)`. Do not change `budget.max_candidates` or `budget.max_parallel` unless the user explicitly describes total budget or pool size.

### Step 2: Validate

Before calling runtime tools, validate the objective, metric, source path, edit
surface, frozen verifier artifacts, and budget against the saved spec. Resolve
ambiguity from repository/runtime evidence or return to Goal Plus Spec
Discovery; do not ask the user to approve entering Search Mode.

### Step 3: Freeze And Create

Call:

```text
goal-plus_search_freeze_spec(spec=<spec>, verifier_artifact_paths=[...])
goal-plus_search_create(frozen_spec_id="<id>")
```

For a later cycle with the same verifier and edit contract, skip refreezing and
call `search_create` with the existing `frozen_spec_id`; the new run
materializes the current source baseline.

Record `run_id`.

### Step 4: Plan And Start Candidate Workspaces

Call:

```text
goal-plus_search_plan_next(run_id="<run_id>", requested_k=<k>)
goal-plus_search_start_batch(run_id="<run_id>", plan_id="<plan_id>", proposals=<optional>)
```

Each returned `CandidateTask` owns an isolated workspace. Candidate work must stay inside that workspace and only modify allowed files.

The default strategy is `agent_guided`, so `plan.requires_agent_proposals` is `true`. You MUST author `plan.planned_k` proposals and pass them to `search_start_batch`:

- Read `plan.official_history.candidates` — each entry carries `candidate_id`, `parent_ids`, `hypothesis`, `intent`, `summary`, `next_ideas`, `key_metrics`, `score`, and `changed_files`.
- Read `plan.proposal_contract`: `count` is the required number of proposals, `must_reference_one_of` lists the candidate_ids each proposal must cite via `parent_candidate_ids` / `history_refs` / `base_candidate_id`.
- For each proposal, decide which prior candidate(s) to build on and write `intent` (one short sentence on the mutation direction), `expected_tradeoff` (what improves / what risks regressing), `instructions` (concrete steps the worker should follow), and `parent_candidate_ids` / `base_candidate_id` (which workspace to derive from).
- First batch (empty history): `must_reference_one_of` is empty, so proposals may set `base_candidate_id=null` and start from source. From the second batch on, every proposal must reference at least one official candidate.

If you switch the spec to a builtin that produces fixed work orders (`independent_branches`, `evolve`, `openevolve`, `mcts`, `random`) or to a Python planner such as `adaptevolve`, `plan.requires_agent_proposals` is `false` and `search_start_batch` must be called without proposals.

### Step 5: Launch OpenCode Task Workers

For `worker_policy.mode == "agent-session-pool"` (the only supported mode):

1. For each candidate you want to dispatch, call `goal-plus_search_start_agent_session(run_id, candidate_id, directive)`. The response includes a `launch` payload: `subagent_type`, `description`, and `prompt`. Use those fields verbatim in the OpenCode Task call below.
2. Launch the subagent with `Task(subagent_type=launch.subagent_type, description=launch.description, prompt=launch.prompt)`. The `launch.prompt` is the only prompt string the worker needs. Do not append or hard-code `run_id` / workspace paths into the prompt — the worker derives those from `search_get_agent_context`.
3. Wait for the OpenCode Task to return. There is no MCP wait call.
4. When a first-time Task returns, bind the runtime session to the OpenCode session id: `goal-plus_search_bind_opencode_session(agent_session_id=session.agent_session_id, opencode_session_id=<Task metadata.sessionId>)`. This mapping is required for same-session continuation.
5. When Task returns, call `goal-plus_search_run_verifier(run_id, candidate_id, "process")` yourself (without `agent_session_id`) to confirm the final score.
6. If the same candidate should keep working in the same OpenCode context and the tier was sufficient, call `goal-plus_search_continue_agent_session(agent_session_id, directive?)`. Launch the returned payload with `Task(task_id=launch.task_id, subagent_type=launch.subagent_type, description=launch.description, prompt=launch.prompt)`. This continues the same candidate/session/workspace; it is not a fork and it does not create a new candidate.
7. If the prior Task hit its step cap, returned no useful verifier evidence, or needs a larger tier/budget, call `goal-plus_search_redispatch_candidate(run_id, candidate_id, directive?, worker_agent_type="SearchCandidateAgentDeep")`. Launch the returned payload like a fresh Task. This creates a new `agent_session_id` for the same candidate workspace and includes resume instructions; it does not mutate candidate policy or create a new candidate.
8. If candidate budget remains and you want new candidates, plan and start the next batch.

Hard host rules:

- OpenCode Task calls are foreground calls. The main agent waits for each Task to return before binding, verifying, continuing, reporting, or promoting.
- `max_parallel` describes the intended worker pool size for planning, but this runtime does not provide an MCP wait loop or lifecycle supervisor.
- There is no supported `timeout` field on Task. Subagents run until their step cap or until the user interrupts.
- The Task prompt must not hard-code `run_id`, `candidate_id`, or workspace paths for the worker to use. The worker must derive them from `search_get_agent_context(agent_session_id)`. The `candidate_id` in the launch description/prompt is a label for OpenCode UI mapping only — context is authoritative.
- `search_continue_agent_session` is only for continuing the same runtime `agent_session_id` after `search_bind_opencode_session`. Do not use it to branch into a new direction that needs a different candidate workspace.
- `search_redispatch_candidate` is state-level resume for the same candidate workspace with a new `agent_session_id`. Use it when the old worker could not finish or when you need to override `worker_agent_type` / `worker_budget` for the next launch.
- Stopping a running subagent is an OpenCode/user interruption concern. There is no MCP abort tool.

Multi-batch sketch:

```text
while budget_remaining:
  plan = search_plan_next(run_id, requested_k=k)
  if plan.requires_agent_proposals:
    proposals = author_proposals(plan)
    tasks = search_start_batch(run_id, plan.plan_id, proposals=proposals)
  else:
    tasks = search_start_batch(run_id, plan.plan_id)

  for task in tasks:
    session = search_start_agent_session(run_id, task.candidate_id, directive)
    result = Task(
      subagent_type=session.launch.subagent_type,
      description=session.launch.description,
      prompt=session.launch.prompt,
    )
    search_bind_opencode_session(session.agent_session_id, result.metadata.sessionId)

  for task in tasks:
    search_run_verifier(run_id, task.candidate_id, "process")  # main final confirm

  # Optional same-node continuation when a completed session is still promising.
  continued = search_continue_agent_session(session.agent_session_id, directive)
  Task(
    task_id=continued.launch.task_id,
    subagent_type=continued.launch.subagent_type,
    description=continued.launch.description,
    prompt=continued.launch.prompt,
  )

  # Optional state-level resume when the prior worker needs a larger tier.
  resumed = search_redispatch_candidate(
    run_id,
    session.candidate_id,
    directive,
    worker_agent_type="SearchCandidateAgentDeep",
  )
  Task(
    subagent_type=resumed.launch.subagent_type,
    description=resumed.launch.description,
    prompt=resumed.launch.prompt,
  )
```

### Step 6: Subagent Autoresearch Contract

The subagent receives only `agent_session_id` and a candidate idea (from `launch.prompt`). It then:

1. Calls `goal-plus_search_get_agent_context(agent_session_id)` to read authoritative `run_id`, `candidate_id`, `workspace`, `allowed_files`, `denied_files`, `budget`, `history`, `iterations`, `results`, and `results_tsv`. The only required MCP calls are `search_get_agent_context` and `search_run_verifier`. Treat these fields and the inherited ledger as the resume context if this is a restarted worker; do not rely on the launch prompt or prior chat transcript for history.
2. Runs an autoresearch loop inside `workspace`: edit allowed files → `goal-plus_search_run_verifier(..., agent_session_id=..., hypothesis="<concise design tested>")` → read ScoreReport → keep the improvement or restore a prior commit after a regression. Each returned verifier report appends to the candidate's iteration history and runtime-owned results ledger; no separate submit step exists.
3. Inspects `workspace/results.tsv` before choosing another variant. The runtime owns this continuous `commit \t <metric_name> \t status \t hypothesis` ledger, commits it in the candidate Git history, inherits it across child/successor workspaces, validates the existing prefix, and appends exactly one row for each returned verifier report. Workers must never create, rewrite, truncate, delete, or manually append it.
4. Ends with the best workspace state checked out and a concise text summary that includes `agent_session_id`, `candidate_id`, best score/metric, best commit hash, changed files, and a short description. This final answer is for OpenCode/main-agent mapping only; no MCP finalize call exists.

You do not pass numeric score targets, baseline scores, or local-verification requests in the worker prompt. The worker reads its own verifier output and decides next steps.

### Step 7: Verify, Select, Report

For every candidate Task that returned:

```text
goal-plus_search_run_verifier(
  run_id,
  candidate_id,
  "process",
  hypothesis="main final verification",
)
```

Then:

```text
goal-plus_search_list_history(run_id, top_n=5, sort_by="score")
goal-plus_search_select(run_id)
```

Show the user the selected candidate and score table summary.

### Step 8: Promote

Only after selection and user review:

```text
goal-plus_search_promote(run_id, selected_candidate_id)
```

When invoked by Goal Plus, return control without generating a report. The
Goal Plus skill calls `goal-plus_search_report` exactly once after its parent
record is terminal. For standalone Search, call it only after promotion.

Promotion exports a patch and should not directly mutate the main source workspace.

## Failure Handling

| Failure | Action |
|---|---|
| MCP tools unavailable | Tell the user the `goal-plus` MCP server is not connected; do not proceed |
| Freeze fails | Fix spec paths/artifacts, then retry freeze |
| Candidate workspace missing | Call status/report; do not recreate by hand |
| Verifier fails | Keep the failure in report; do not edit verifier |
| No passing candidates | Report scores and failure classes; ask whether to run another batch |
| User wants to stop | Stop launching new Tasks and let OpenCode interrupt running Tasks; there is no MCP abort |

## k_module Smoke Pattern

For a quick runtime smoke test, load `examples/k_module_search_spec.json`, freeze `tests/fixtures/k_module_problem/evaluator.py`, create 4 candidates, dispatch deterministic edits, verify, select, and report. This is a control-plane test, not a proof of search quality.

## Multi-Batch Examples

The bundled `circle_packing` and `signal_processing` specs use `agent-session-pool`. For `max_parallel=2` and 4 total subagents, plan/start candidates in batches and launch each OpenCode Task as a foreground call. At run budget exhaustion, stop launching new Tasks and report the best candidates. There is no MCP abort step before reporting.
