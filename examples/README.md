# Goal Plus Search Examples

The example specs are small local scenarios for exercising `/goal-plus` after it
upgrades a measurable task into Search Mode.

## Automated ST Coverage

Each scenario prompt below has a paired system test under `tests/st/`. The tests
drive `opencode run --command goal-plus "<prompt>"` in a temporary project root
and parse a machine-readable JSON report from the main agent's final message.

- Prompts: `tests/st/prompts/<scenario>.md`
- Tests: `tests/st/test_st_scenarios.py`
- Output contract: `tests/st/prompts/_schema.md`

Run them with:

```bash
pytest -m st                       # all 6 scenarios
pytest -m st -k k_module_smoke     # single scenario
```

Tests are skipped by default. They require `opencode` on PATH and the
`search-runtime` MCP server connected (`opencode mcp list`).


| Spec | Fixture | Worker | Layout |
|---|---|---|---|
| `k_module_search_spec.json` | `tests/fixtures/k_module_problem` | `AnySearchAgentFlash` (15 steps) | 2 candidates, pool=2, single batch |
| `search-mode/k_module_adaptevolve_search_spec.json` | `tests/fixtures/k_module_problem` | AdaptEvolve dynamic tier (starts with `AnySearchAgentFlash`) | 1 candidate, pool=1, smoke test |
| `search-mode/k_module_openevolve_search_spec.json` | `tests/fixtures/k_module_problem` | OpenEvolve-style sampling with `AnySearchAgentFlash` | 2 candidates, pool=1, two sequential batches |
| `circle_packing_search_spec.json` | `tests/fixtures/circle_packing` | `AnySearchAgentFlash` (15 steps) | 4 candidates, pool=2, two batches |
| `signal_processing_search_spec.json` | `tests/fixtures/signal_processing` | `AnySearchAgent` (50 steps) | 8 candidates, pool=4, two batches |
| `swe_bench_20212_search_spec.json` | `tests/fixtures/swe_bench_20212` | `AnySearchAgent` (50 steps) | 4 candidates, pool=2, single batch |

For each example, start through `/goal-plus`. The goal-plus layer records the
raw goal, triage, frozen verifier confirmation, and final raw-goal audit. Once
the task enters Search Mode, create the run, call `search_plan_next(run_id, k)`,
then start the returned plan with `search_start_batch(run_id, plan_id)`. For
multi-batch examples, plan + start the next batch after the first batch
finishes. The runtime enforces isolated workspaces and verifier-owned scoring;
the active strategy defines how later candidates should derive from history.

Before requesting a follow-up batch, the host can call `search_list_history(run_id)` to recover a compact JSON summary of the best candidates so far.

`strategy.worker_mode` is always `agent-session-pool`. Candidate execution always goes through an OpenCode Task launched from a runtime context handle: call `search_start_agent_session(run_id, candidate_id, directive)`, launch the configured subagent with the returned `launch` payload, then bind the Task `metadata.sessionId` with `search_bind_opencode_session`.

Subagents run until their OpenCode step cap hits or the user interrupts them. There are no per-session or run-level time deadlines. Launch candidate subagents as foreground OpenCode Task calls and wait for each Task to return before binding, verifying, continuing, or reporting.

## Step Tiers

`strategy.worker_agent_type` picks one of four OpenCode subagent variants. The variant fixes the host-enforced step cap. Python strategy plugins may return a `worker_policy` override for the next plan; the `search_start_agent_session` launch payload is the authoritative subagent tier for that candidate.

| Variant | Steps | Use when |
|---|---|---|
| `AnySearchAgentFlash` | 15 | Smoke tests, toy tasks, cheap iterations (k_module, small fixtures) |
| `AnySearchAgent` (default) | 50 | Standard autoresearch loop |
| `AnySearchAgentDeep` | 100 | Sustained iteration on harder problems |
| `AnySearchAgentExtraDeep` | 150 | Extensive search, complex fixtures |

When the step cap is reached OpenCode injects a system prompt instructing the agent to summarize and stop — the session ends cleanly without a hard kill.

## Budget Semantics

Each `SearchSpec` must include an explicit `budget`; there are no runtime defaults.

```json
{
  "budget": {
    "max_candidates": 4,
    "max_parallel": 2
  }
}
```

- `max_candidates`: total candidate workspaces allowed for the run. Enforced by `search_plan_next` / `search_start_batch`.
- `max_parallel`: batch planning hint. The runtime records it in the spec, but Task calls are foreground and the runtime does not supervise workers.
- `max_tokens`: optional worker-level cap.

Freeze the matching evaluator as the verifier artifact:

```text
tests/fixtures/circle_packing/evaluator.py
tests/fixtures/signal_processing/evaluator.py
tests/fixtures/k_module_problem/evaluator.py
tests/fixtures/swe_bench_20212/evaluator.py
```

## Strategy Modes

The default strategy is `agent_guided`: the runtime exposes the official candidate history and the main agent authors the next batch (pick parents, write one proposal per slot). The bundled example specs pin `independent_branches` to keep their demo flows independent of history; switch a copied spec to other modes by setting `strategy.name`:

```json
{
  "strategy": {
    "name": "agent_guided",
    "driver": "builtin",
    "worker_mode": "agent-session-pool",
    "worker_agent_type": "AnySearchAgent",
    "history_policy": {"scope": "top_n", "top_n": 5}
  }
}
```

Comparison:

| Strategy | Parent picker | `requires_agent_proposals` | First batch | Use when |
|---|---|---|---|---|
| `agent_guided` (default) | Main agent | `true` | Empty history → no reference constraint, proposals may start from source | Let the main agent judge which prior candidates to build on |
| `independent_branches` | None — all from source | `false` | All from source | Baseline, no lineage |
| `evolve` | Runtime: best-score parent + top-N inspirations | `false` | Bootstrap from source | OpenEvolve-style fixed parent selection |
| `openevolve` | Runtime: sampled parent from scored population/archive + inspirations | `false` | Bootstrap from source | Minimal OpenEvolve-style parent/archive/inspiration sampling |
| `mcts` | Runtime: best-score frontier | `false` | Bootstrap from source | MCTS-style expansion (placeholder planner) |
| `random` | Runtime: random scored parent (seedable via `strategy.config.seed`) | `false` | Bootstrap from source | Cheap random-walk baseline |
| `adaptevolve` | Python plugin: best-score parent + confidence-routed worker tier | `false` | Bootstrap from source with `AnySearchAgentFlash` | Adaptive compute allocation around evolve-style mutations |

Notes:

- In `agent_guided`, `search_plan_next` returns `proposal_contract.must_reference_one_of` listing the candidate_ids each proposal must cite. The first batch has empty history so the constraint is empty; from the second batch on every proposal must reference at least one official candidate.
- In `evolve` / `openevolve` / `mcts` / `random`, the runtime selects the parent internally and emits fixed `work_orders`; `search_start_batch` must be called without proposals.
- In `adaptevolve`, the Python strategy plugin emits fixed `work_orders` and a dynamic `worker_policy`. The runtime records that policy in candidate metadata and uses it when building the OpenCode Task launch payload.
- `independent_branches` ignores history entirely — every candidate starts from the frozen source workspace.

## Running an example

Start OpenCode:

```bash
opencode
```

Then paste a plain-language prompt into `/goal-plus`. The host loads the
goal-plus skill first and uses the internal `search` skill only after Search
Mode starts. For non-interactive runs, include explicit text confirming the
frozen verifier, metric, edit surface, and promotion rule.

### circle_packing — fork-style continuation smoke test

Use this when you want to verify the "subagent finished, then main starts it again from the same node" path:

```
Load examples/circle_packing_search_spec.json and freeze tests/fixtures/circle_packing/evaluator.py.

Run one circle_packing candidate and then continue the same OpenCode session:
  1. freeze_spec → create → plan_next(k=1) → start_batch
  2. call search_start_agent_session for c001
  3. launch Task with session.launch; use a directive like "build a hexagonal or staggered lattice, then tune radii to improve total packed area"
  4. when Task returns, call search_bind_opencode_session(session.agent_session_id, Task metadata.sessionId)
  5. run search_run_verifier(run_id, "c001", "process") from the main agent
  6. call search_continue_agent_session(session.agent_session_id, directive="continue the same circle_packing candidate from the current workspace; tune radii and repair overlaps; do not create a new candidate")
  7. launch Task again with task_id=continued.launch.task_id and the rest of continued.launch
  8. when Task returns, run search_run_verifier(run_id, "c001", "process") again
  9. call search_list_history and search_report

This is the fork-style smoke test for the current implementation: it continues the same OpenCode session with Task task_id instead of using OpenCode Session.fork. Do not create a second agent session for c001. Report run_id, agent_session_id, opencode_session_id, both verifier scores, score delta, and report path.
```

### circle_packing — two batches, AnySearchAgentFlash

```
Load examples/circle_packing_search_spec.json. The spec already sets max_candidates=4, max_parallel=2, worker_agent_type=AnySearchAgentFlash (15 step cap). Freeze tests/fixtures/circle_packing/evaluator.py as the verifier artifact. Then run the full search end-to-end with TWO batches:

Batch 1 (c001, c002):
  - c001: hexagonal lattice (rows of offset circles, e.g. 6+5+6+5+4=26 or 7+6+7+6=26, varied radius per row)
  - c002: square grid with shrink-to-fit (start uniform, iteratively shrink radii to remove overlaps and maximize sum)

Wait for both to finish, run run_verifier on each, then plan_next(k=2) → start_batch for batch 2.

Batch 2 (c003, c004):
  - c003: concentric rings with optimized ring radii (try 1+6+12+7 or 1+8+16+1 type layouts, tune ring radii)
  - c004: boundary-hugging approach (pack circles along the perimeter first, then fill center)

After both batches terminate, run run_verifier on c003 and c004 yourself (no agent_session_id, auto-attribute), then select across all 4 candidates and report.

For each Task: use the runtime launch payload, then bind the returned Task metadata.sessionId. Do not hard-code run_id/candidate_id/workspace.

Report at the end: run_id, all 4 candidate scores + iteration counts, selected candidate_id, and report.md path.
```

### circle_packing — random strategy, two batches

Same fixture as above but the spec uses the `random` strategy so batch 2 derives from a runtime-picked parent instead of fresh source branches. Copy `circle_packing_search_spec.json` and set `"strategy": {"name": "random", "config": {"seed": 42}}` (seed optional; omit for non-deterministic parent pick).

```
Load a copy of examples/circle_packing_search_spec.json with strategy.name set to "random" (keep max_candidates=4, max_parallel=2, worker_agent_type=AnySearchAgentFlash; optionally set strategy.config.seed=42 for a reproducible parent pick). Freeze tests/fixtures/circle_packing/evaluator.py as the verifier artifact. Then run the full search end-to-end with TWO batches:

Batch 1 (c001, c002 — random bootstrap, both derive from source):
  - c001: hexagonal lattice (rows of offset circles, e.g. 6+5+6+5+4=26 or 7+6+7+6=26, varied radius per row)
  - c002: square grid with shrink-to-fit (start uniform, iteratively shrink radii to remove overlaps and maximize sum)

Wait for both to finish, run run_verifier on each, then plan_next(k=2) → start_batch for batch 2. The runtime will randomly pick one of {c001, c002} as the parent; batch 2 workspaces are copied from that parent.

Batch 2 (c003, c004 — both mutate the runtime-picked parent):
  - c003: concentric rings with optimized ring radii (try 1+6+12+7 or 1+8+16+1 type layouts, tune ring radii)
  - c004: boundary-hugging approach (pack circles along the perimeter first, then fill center)

After both batches terminate, run run_verifier on c003 and c004 yourself (no agent_session_id, auto-attribute), then select across all 4 candidates and report. Report strategy_trace.parent_candidate_id from the batch 2 plan so the random pick is visible.

For each Task: use the runtime launch payload, then bind the returned Task metadata.sessionId. Do not hard-code run_id/candidate_id/workspace.

Report at the end: run_id, batch 2 parent_candidate_id, all 4 candidate scores + iteration counts, selected candidate_id, and report.md path.
```

### k_module — smoke test, AnySearchAgentFlash

```
Load examples/k_module_search_spec.json. The spec sets max_candidates=2, max_parallel=2, worker_agent_type=AnySearchAgentFlash. Freeze tests/fixtures/k_module_problem/evaluator.py and run end-to-end: freeze_spec → create → plan_next(k=2) → start_batch → start 2 sessions → Task → bind_opencode_session → run_verifier on each → select → report.
```

### k_module — AdaptEvolve smoke test

```
Load examples/search-mode/k_module_adaptevolve_search_spec.json. Freeze tests/fixtures/k_module_problem/evaluator.py and run the smallest end-to-end AdaptEvolve case: freeze_spec → create → plan_next(k=1) → start_batch → start_agent_session → Task with session.launch → bind_opencode_session → run_verifier → select → report. Confirm that the plan strategy_trace shows selected_worker_agent_type and that the Task launch uses AnySearchAgentFlash for the first bootstrap candidate.
```

### k_module — OpenEvolve sampling smoke test

```
Load examples/search-mode/k_module_openevolve_search_spec.json. Freeze tests/fixtures/k_module_problem/evaluator.py and run two sequential one-candidate batches: batch 1 does bootstrap from source, then run_verifier; batch 2 calls plan_next(k=1) again so openevolve samples a parent from scored history and emits inspiration context. Start the second batch without proposals, launch Task with session.launch, bind, verify, select, and report. Confirm that the second plan strategy_trace shows selection_rule=openevolve sampled parent plus inspirations and includes parent_candidate_id.
```

### signal_processing — multi-batch, AnySearchAgent

```
Load examples/signal_processing_search_spec.json (max_candidates=8, max_parallel=4, AnySearchAgent 50 steps). Freeze tests/fixtures/signal_processing/evaluator.py. Plan + start 4 candidates, wait for each OpenCode Task to return, then plan + start the next 4. Report the best score after both batches.
```

## SWE-bench Style Fixture

`swe_bench_20212_search_spec.json` wraps a SWE-bench bug fix (`sympy__sympy-20212`) instead of a multi-batch optimization. The candidate's job is to patch `evaluate_power` in `tests/fixtures/swe_bench_20212/initial_program.py` so that `evaluate_power(ZERO, NEG_INFINITY)` returns `COMPLEX_INFINITY`. See `tests/fixtures/swe_bench_20212/README.md` for the bug context and the local verification recipe (no sympy or docker required).

```
Load examples/swe_bench_20212_search_spec.json. Freeze tests/fixtures/swe_bench_20212/evaluator.py. Request 4 candidates. After submitting and verifying them, inspect summaries and FAIL_TO_PASS / PASS_TO_PASS results. Stop after report generation and do not promote.
```

Quick local sanity check (no runtime needed):

```bash
cd tests/fixtures/swe_bench_20212 && python3 -c "from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), indent=2))"
```

The buggy baseline returns `combined_score = 0.0`; after applying the two-line gold patch described in the fixture README the score reaches `1.0`.
