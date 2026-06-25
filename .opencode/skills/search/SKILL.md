---
name: search
description: >
  Run MCP-controlled Search Mode for measurable multi-candidate coding tasks.
  Use when the user invokes /search or asks to try several candidate fixes,
  optimizations, or configurations under a frozen verifier.
argument-hint: >
  Objective, source path, allowed files, verifier command/artifacts, budget.
---

# Agentic Search Skill

## What This Skill Does

This skill guides the host agent through Search Mode while the Search MCP Runtime owns durable state, candidate workspaces, verifier execution, best-seen selection, and promotion artifacts.

The host agent controls progress by calling MCP tools. Workers are just candidate executors; in V0 the main agent can act as the worker by editing each candidate workspace directly.

## Code Agent Intake

In an interactive coding session, `/search` usually starts from an unstructured user request, not from a prewritten example spec. The host agent should first translate the request into a SearchSpec-shaped job:

- clarify the measurable objective
- identify the source path and editable surface
- identify files that must be frozen or denied, especially tests, benchmarks, evaluators, and configs
- choose or propose a verifier command that can score candidates
- choose an explicit budget

If the objective, verifier, or edit surface is ambiguous or risky, summarize the proposed SearchSpec and ask before freezing. If the user points to a bundled example spec, load it directly.

Good `/search` triggers:

- performance optimization with a benchmark or profiler signal
- algorithmic tasks with several plausible implementations
- bug fixes where multiple hypotheses can be tested against the same failing test
- parser, scraper, prompt, ranking, or heuristic improvements with measurable fixtures
- configuration or hyperparameter search with a bounded edit surface
- "try a few approaches", "explore variants", "find a better implementation", or similar user language
- local benchmark improvement for kernels, inference paths, planners, schedulers, or search procedures

Poor `/search` triggers:

- a small deterministic edit where one direct patch is enough
- broad refactors without a crisp verifier
- tasks where candidates would need to edit unrelated files freely
- tasks whose verifier depends mainly on private external services or manual judgment
- requests where the correct answer is analysis or documentation rather than candidate execution

Candidate artifacts should help the host decide follow-up work. A useful artifact summary names the approach, changed files, observed verifier result, tradeoffs, and concrete next ideas. Do not treat the summary as a trusted score; runtime verifier results remain authoritative.

## Tool Names In OpenCode

The local MCP server is configured as `search-runtime`, so OpenCode exposes runtime tools with this prefix:

| Runtime tool | OpenCode tool name |
|---|---|
| `search_freeze_spec` | `search-runtime_search_freeze_spec` |
| `search_create` | `search-runtime_search_create` |
| `search_status` | `search-runtime_search_status` |
| `search_list_history` | `search-runtime_search_list_history` |
| `search_plan_next` | `search-runtime_search_plan_next` |
| `search_start_batch` | `search-runtime_search_start_batch` |
| `search_next_batch` | `search-runtime_search_next_batch` |
| `search_prepare_worker` | `search-runtime_search_prepare_worker` |
| `search_get_worker_context` | `search-runtime_search_get_worker_context` |
| `search_submit_candidate` | `search-runtime_search_submit_candidate` |
| `search_run_verifier` | `search-runtime_search_run_verifier` |
| `search_select` | `search-runtime_search_select` |
| `search_report` | `search-runtime_search_report` |
| `search_promote` | `search-runtime_search_promote` |
| `search_abort` | `search-runtime_search_abort` |

If these tools are unavailable, stop and report that the MCP server is not connected. Do not simulate runtime state in chat.

## Required Discipline

1. Do not start candidate execution before the SearchSpec and verifier artifacts are frozen.
2. Do not modify verifier files during candidate execution.
3. Do not edit the main source workspace while exploring candidates.
4. Do not accept worker-reported scores. Always call `search_run_verifier`.
5. Do not promote by manually copying files. Use `search_promote`; it exports a patch/report.
6. If a candidate touches denied files or files outside the edit surface, submit it anyway and let runtime mark it failed.

## Workflow

### Step 1: Probe Read-Only Context

Read enough files to identify:

- objective
- source path
- allowed edit files
- denied verifier/config files
- process verifier command
- promotion verifier command, if any
- budget: `max_candidates`, `max_parallel`, `wall_clock_seconds`

For V0, prefer small deterministic verifiers. Bundled concrete specs live in `examples/`: `k_module_search_spec.json` is the shortest smoke test, while `circle_packing_search_spec.json` and `signal_processing_search_spec.json` are larger multi-batch examples.

Candidate dependencies are part of the verifier environment contract. If a candidate imports a package that is unavailable in the verifier environment, submit and verify it anyway so the runtime records the failure; do not silently switch the task framing unless the user asks for a dependency-constrained search.

### Step 2: Draft SearchSpec

Create a JSON-compatible spec. Minimum shape:

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
      "command": ["search-runtime-internal", "check-frozen-hashes"]
    }
  ],
  "budget": {
    "max_candidates": 4,
    "max_parallel": 4,
    "wall_clock_seconds": 300
  },
  "strategy": {
    "name": "independent_branches",
    "driver": "builtin",
    "worker_mode": "main-agent-search-direct",
    "worker_agent_type": null,
    "worker_timeout_seconds": 600,
    "worker_local_verifier_max_runs": 0,
    "history_policy": {
      "scope": "top_n",
      "top_n": 5
    }
  }
}
```

For bundled examples, load the matching JSON file from `examples/` instead of embedding case-specific specs in this skill.

Budget is explicit and required; the runtime does not invent defaults. In V0, `max_candidates` is enforced by the runtime, while `max_parallel`, `wall_clock_seconds`, `max_worker_seconds`, and `max_tokens` are used as host/worker scheduling limits and recorded intent.

Strategy is a run-level MCP setting. The main agent may remember more chat history, but candidate generation should follow the official strategy plan returned by `search_plan_next`.

`strategy.worker_mode` controls who performs candidate execution:

- `main-agent-search-direct`: the main agent edits candidate workspaces directly.
- `sub-agent-search-dispatch`: the main agent must call `search_prepare_worker` for each candidate and dispatch work to a subagent/worker. If `worker_agent_type` is set, use it as the OpenCode `subagent_type`. Candidate artifacts must include `dispatch_id` and `context_hash`.
- `auto`: runtime resolves the effective mode and returns it in `worker_policy`.

`strategy.worker_timeout_seconds` controls the default candidate worker timebox. Default is 600 seconds. The main agent may set a smaller or larger value in the spec, or pass `timeout_seconds` to `search_prepare_worker` for a per-dispatch override. When dispatching subagents, treat this as the maximum time to wait before collecting best-so-far artifacts and moving on.

`strategy.worker_local_verifier_max_runs` controls whether a worker may run the process verifier command, or an equivalent local scorer, while exploring one candidate. Default is 0, which means worker-local scoring is disabled. In the default dispatch mode, workers analyze and write candidate code, may run non-scoring static checks such as `py_compile`, and leave all actual verification to the main agent/runtime-owned `search_run_verifier` calls after submission.

Common strategy names:

- `independent_branches`: each candidate starts from source.
- `agent_guided`: runtime returns official history and asks the main agent to submit proposals.
- `evolve`: runtime chooses a best parent and inspirations; candidates derive from that parent.
- `mcts`: runtime returns a frontier expansion contract.

### Step 3: Confirm With User

Before calling runtime tools, summarize:

- objective and metric
- source path
- allowed and denied files
- verifier artifact paths to freeze
- candidate budget

Ask for confirmation if any of these are ambiguous or risky. For a direct `/search` smoke run on the k_module fixture, a short confirmation is enough.

### Step 4: Freeze Spec And Create Run

Call:

1. `search-runtime_search_freeze_spec`
   - `spec`: the confirmed spec object
   - `verifier_artifact_paths`: absolute or workspace-relative verifier files, e.g. `["tests/fixtures/k_module_problem/evaluator.py"]`
2. `search-runtime_search_create`
   - `frozen_spec_id`: returned by freeze

Record the returned `run_id` in the chat.

### Step 5: Plan And Create Candidate Workspaces

Preferred strategy-aware flow:

1. Call `search-runtime_search_plan_next(run_id, requested_k)`.
2. Read the returned `strategy`, `worker_policy`, `official_history`, `derivation_policy`, `strategy_trace`, and either:
   - if `requires_agent_proposals` is `false`, call `search-runtime_search_start_batch(run_id, plan_id)`;
   - if `requires_agent_proposals` is `true`, submit proposals to `search-runtime_search_start_batch(run_id, plan_id, proposals)`.

Before dispatching workers, explicitly note `worker_policy.timeout_seconds` and `worker_policy.local_validation_rule`. These are MCP-provided execution limits for the batch.

Each proposal should include:

```json
{
  "parent_candidate_ids": ["c003"],
  "base_candidate_id": "c003",
  "intent": "concrete candidate idea",
  "expected_tradeoff": "what should improve and what might regress",
  "instructions": ["worker-facing instruction"],
  "history_refs": ["c005"]
}
```

Compatibility shortcut:

- `search-runtime_search_next_batch(run_id, k)` calls plan/start automatically for fixed work-order strategies such as `independent_branches` and `evolve`.
- Do not use `search_next_batch` for `agent_guided`; it requires explicit proposals.

For each returned `CandidateTask`:

- Work only in `workspace`.
- Use `workspace/.tmp/` only for notes, static drafts, and non-scoring helper material.
- Do not create or run scratch experiment scripts, scorer clones, validation harnesses, parameter sweeps, or benchmark scripts in worker mode.
- Do not use `/tmp`, user home directories, or other external scratch locations during candidate work.
- Do not delete, move, reset, restore, or clean files. Forbidden destructive commands include `rm`, `mv`, `rmdir`, `unlink`, `trash`, `find -delete`, `git clean`, `git reset`, `git restore`, and `git checkout`.
- Modify only `allowed_files`.
- Do not edit `denied_files`.
- Respect `plan_id`, `base_candidate_id`, `parent_candidate_ids`, and `proposal` metadata. If a plan says a candidate must derive from a parent, the runtime-created workspace already starts from that parent.
- Write candidate notes if useful, but runtime does not require them.

### Step 5.5: Prepare Worker Dispatches

Follow the `worker_policy` returned by `search_plan_next`:

- If `worker_policy.mode` is `main-agent-search-direct`, the main agent may edit candidate workspaces directly and skip this step.
- If `worker_policy.mode` is `sub-agent-search-dispatch`, this step is required before candidate execution.

For `sub-agent-search-dispatch`, use the two-channel dispatch protocol:

1. For each `CandidateTask`, call `search-runtime_search_prepare_worker`. Optionally pass `timeout_seconds` if this worker should use a different timebox from `strategy.worker_timeout_seconds`.
   - `run_id`: current run
   - `candidate_id`: target candidate
   - `main_directive`: the main agent's explicit worker-facing intent. It may be either a plain string or a structured object with fields such as goal, why this candidate exists, suggested direction, expected output, and notes. Do not include score targets, baseline scores, local verification requests, or instructions to beat a numeric score in a worker directive.
2. Dispatch to `worker_policy.subagent_type` if present. In this project, bundled dispatch specs use `AnySearchAgent`.
3. Pass the returned `worker_brief` or at least `dispatch_id`, `run_id`, and `candidate_id` to the worker.
4. Instruct the worker to call `search-runtime_search_get_worker_context(dispatch_id)` as its first step.

The main agent's directive is useful guidance. The MCP worker context is authoritative for workspace path, allowed/denied files, strategy mode, lineage, official visible history, verifier commands, scratch directory, and artifact requirements. If they conflict, the worker must report the conflict and follow the MCP context.

The main agent is responsible for collection discipline. The timeout is a host-side deadline, not a guarantee that OpenCode will kill a worker process. If a worker times out or returns without submitting, submit a failure/timeout artifact or explicitly salvage the candidate workspace and mark the summary honestly. Do not leave the run half-collected.

The main agent must include the worker execution limits in the subagent prompt: timeout/deadline, no worker-local scoring by default, no score-target directive, and the requirement that final candidate code be bounded and fast.

Worker prompt skeleton:

```text
You are working on search candidate <candidate_id>.

Dispatch ID: <dispatch_id>
Run ID: <run_id>
Candidate ID: <candidate_id>

First call:
search-runtime_search_get_worker_context(dispatch_id="<dispatch_id>")

Treat MCP context as authoritative. Work only in the workspace returned by MCP.
Use workspace/.tmp only for notes/static drafts/non-scoring helper material. Do not create or run scratch experiment scripts, scorer clones, validation harnesses, parameter sweeps, or benchmark scripts.
Do not use /tmp or external scratch directories.
Do not delete, move, reset, restore, or clean files. Do not use rm, mv, rmdir, unlink, trash, find -delete, git clean, git reset, git restore, or git checkout.
Respect the timeout/deadline in MCP context.
Do not run the process verifier or any equivalent local scorer. You may run non-scoring static checks such as py_compile.
If the main directive includes score targets or baseline scores, treat them as main-agent context only; do not run local scoring to satisfy them.
Keep the final allowed-file change bounded and fast; do not embed long searches or parameter sweeps in the final implementation.
Return or submit the best-so-far artifact containing candidate_id, dispatch_id, context_hash, status, summary, and any next ideas.
```

`search_prepare_worker` writes durable audit files under `.search/runs/<run_id>/dispatches/`. This lets the main agent later inspect exactly what was sent to a worker, even if chat context is lost.

### Step 6: Submit Candidates

After each candidate workspace is ready, call `search-runtime_search_submit_candidate`:

```json
{
  "run_id": "<run_id>",
  "candidate_id": "<candidate_id>",
  "artifact": {
    "candidate_id": "<candidate_id>",
    "dispatch_id": "<dispatch_id if worker dispatch was used>",
    "context_hash": "<context_hash returned by search_get_worker_context>",
    "status": "patch_ready",
    "summary": "short description of what was tried, why, result/tradeoff if known",
    "next_ideas": ["follow-up idea if useful"]
  }
}
```

Do not include score claims in the summary unless they come from a main-agent/runtime `search_run_verifier` result. If the worker could not call MCP directly, the main agent may submit this artifact on its behalf, but should preserve the worker's dispatch id, context hash, and summary. The main agent must ensure `search_run_verifier` is called for every submitted candidate before selection.

### Step 7: Verify And Select

For every submitted candidate, call:

```text
search-runtime_search_run_verifier(run_id, candidate_id, "process")
```

Then call:

```text
search-runtime_search_list_history(run_id, top_n=5, sort_by="score")
search-runtime_search_select(run_id)
search-runtime_search_report(run_id)
```

Use `search-runtime_search_list_history` before follow-up batches or final selection when the active chat context is incomplete. It returns a compact JSON summary of top candidates, including candidate summaries, scores, key metrics, changed files, failures, and verifier logs.

For follow-up batches, call `search-runtime_search_plan_next` again. Treat the returned plan as the official next-step search contract.

Show the user the selected candidate, score table summary, and report path.

### Step 8: Promote

Only after selection and user review, call:

```text
search-runtime_search_promote(run_id, selected_candidate_id)
```

Promotion exports a patch. It should not directly mutate the main source workspace.

## k_module Smoke Run Pattern

For a quick runtime smoke test:

1. Load the spec from `examples/k_module_search_spec.json`.
2. Freeze `tests/fixtures/k_module_problem/evaluator.py`.
3. Create 4 candidates.
4. In each candidate workspace, edit `initial_program.py` to one of:
   - baseline unchanged
   - only `loader = "csv_reader"`
   - loader + `preprocess = "normalize"`
   - full target: `loader="csv_reader"`, `preprocess="normalize"`, `algorithm="quicksort"`, `formatter="json"`
5. Submit and verify all.
6. Select should choose the full target candidate with score `1.0`.

This is a toy control-plane test, not a proof of search quality.

## Multi-Batch Example Pattern

The bundled `circle_packing` and `signal_processing` specs use `max_candidates=8` and `max_parallel=4`. This is not a runtime-enforced round protocol. It means the run can create at most 8 candidates total, and the host should request at most 4 candidates at once.

For these examples:

1. Load the selected spec from `examples/`.
2. Freeze the matching evaluator:
   - `tests/fixtures/circle_packing/evaluator.py`
   - `tests/fixtures/signal_processing/evaluator.py`
3. Create the run.
4. Call `search-runtime_search_plan_next(run_id, 4)`, then `search-runtime_search_start_batch(run_id, plan_id)` for the first batch. `search-runtime_search_next_batch(run_id, 4)` is acceptable for these default independent specs.
5. Submit and verify those candidates.
6. Inspect verifier results and candidate artifact summaries. If needed, call `search-runtime_search_list_history(run_id, top_n=5, sort_by="score")` to recover compact durable history across all earlier candidates.
7. If more exploration is useful, call `search-runtime_search_plan_next(run_id, 4)` again and start the returned plan. For `agent_guided`, submit explicit proposals; for fixed strategies, start the returned work orders.
8. Submit and verify the later candidates, then select and report.

The runtime records candidates, workspaces, verifier logs, and best-seen state. The host agent decides what follow-up request to give later workers based on earlier evidence.

## Failure Handling

| Failure | Action |
|---|---|
| MCP tools unavailable | Tell the user the `search-runtime` MCP server is not connected; do not proceed |
| Freeze fails | Fix spec paths/artifacts, then retry freeze |
| Candidate workspace missing | Call status/report; do not recreate by hand |
| Verifier fails | Keep the failure in report; do not edit verifier |
| No passing candidates | Report scores and failure classes; ask whether to run another batch |
| User wants to stop | Call `search-runtime_search_abort(run_id, reason)` |
