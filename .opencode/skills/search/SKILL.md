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
2. Read the returned `strategy`, `official_history`, `derivation_policy`, `strategy_trace`, and either:
   - if `requires_agent_proposals` is `false`, call `search-runtime_search_start_batch(run_id, plan_id)`;
   - if `requires_agent_proposals` is `true`, submit proposals to `search-runtime_search_start_batch(run_id, plan_id, proposals)`.

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
- Modify only `allowed_files`.
- Do not edit `denied_files`.
- Respect `plan_id`, `base_candidate_id`, `parent_candidate_ids`, and `proposal` metadata. If a plan says a candidate must derive from a parent, the runtime-created workspace already starts from that parent.
- Write candidate notes if useful, but runtime does not require them.

V0 worker mode:

- The main agent may edit candidate workspaces directly.
- Native subagents/headless workers are optional and not required.

### Step 6: Submit Candidates

After each candidate workspace is ready, call `search-runtime_search_submit_candidate`:

```json
{
  "run_id": "<run_id>",
  "candidate_id": "<candidate_id>",
  "artifact": {
    "candidate_id": "<candidate_id>",
    "status": "patch_ready",
    "summary": "short description of the hypothesis/result"
  }
}
```

Do not include unverifiable score claims in the summary.

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
