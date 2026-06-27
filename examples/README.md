# Search Examples

The example specs are small local scenarios for exercising the Search MCP runtime.

| Spec | Fixture | Worker | Layout |
|---|---|---|---|
| `k_module_search_spec.json` | `tests/fixtures/k_module_problem` | `AnySearchAgentFlash` (15 steps) | 2 candidates, pool=2, single batch |
| `circle_packing_search_spec.json` | `tests/fixtures/circle_packing` | `AnySearchAgentFlash` (15 steps) | 4 candidates, pool=2, two batches |
| `signal_processing_search_spec.json` | `tests/fixtures/signal_processing` | `AnySearchAgent` (50 steps) | 8 candidates, pool=4, two batches |
| `swe_bench_20212_search_spec.json` | `tests/fixtures/swe_bench_20212` | `AnySearchAgent` (50 steps) | 4 candidates, pool=2, single batch |

For each example, create the run, call `search_plan_next(run_id, k)`, then start the returned plan with `search_start_batch(run_id, plan_id)`. For multi-batch examples, plan + start the next batch after the first batch finishes. The runtime enforces isolated workspaces and verifier-owned scoring; the active strategy defines how later candidates should derive from history.

Before requesting a follow-up batch, the host can call `search_list_history(run_id)` to recover a compact JSON summary of the best candidates so far.

`strategy.worker_mode` is always `agent-session-pool`. Candidate execution always goes through a managed subagent session: call `search_start_agent_session(run_id, candidate_id, directive, budget?)`, launch the configured subagent with the returned `agent_session_id`, and supervise progress with `search_wait_agent_events`.

`strategy.worker_timeout_seconds` is the default MCP per-session wall-clock budget. The runtime truncates session deadlines to the remaining run budget. For OpenCode, this is not a `Task` timeout; start OpenCode with `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true` and launch candidate subagents with `background: true` whenever `max_parallel > 1`.

## Step Tiers

`strategy.worker_agent_type` picks one of four OpenCode subagent variants. The variant fixes the host-enforced step cap; runtime cannot override it per call.

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
    "max_parallel": 2,
    "wall_clock_seconds": 300
  }
}
```

- `max_candidates`: total candidate workspaces allowed for the run. Enforced by `search_plan_next` / `search_start_batch`.
- `max_parallel`: maximum active agent sessions. Enforced by `search_start_agent_session`.
- `wall_clock_seconds`: run-level time budget. `search_wait_agent_events` wakes on run deadline and `abort_all_agent_sessions` stops active work.
- `max_worker_seconds` and `max_tokens`: optional worker-level caps.

Freeze the matching evaluator as the verifier artifact:

```text
tests/fixtures/circle_packing/evaluator.py
tests/fixtures/signal_processing/evaluator.py
tests/fixtures/k_module_problem/evaluator.py
tests/fixtures/swe_bench_20212/evaluator.py
```

## Strategy Modes

Example specs currently use the default `independent_branches` strategy. To test strategy-aware follow-up behavior, add a structured strategy block to a copied spec:

```json
{
  "strategy": {
    "name": "agent_guided",
    "driver": "builtin",
    "worker_mode": "agent-session-pool",
    "worker_agent_type": "AnySearchAgent",
    "worker_timeout_seconds": 240,
    "history_policy": {"scope": "top_n", "top_n": 5}
  }
}
```

In `agent_guided`, `search_plan_next` returns a proposal contract and `search_start_batch` must receive explicit proposals. In `evolve`, the runtime selects a parent and inspirations, then starts candidate workspaces from the selected parent.

## Running an example

Start OpenCode (must set the env var when `max_parallel > 1`):

```bash
OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true opencode
```

Then paste a plain-language prompt into the Build agent. The host loads the `search` skill automatically based on description match — there is no `/search` slash command.

### circle_packing — two batches, AnySearchAgentFlash

```
Load examples/circle_packing_search_spec.json. The spec already sets max_candidates=4, max_parallel=2, worker_agent_type=AnySearchAgentFlash (15 step cap), worker_timeout_seconds=240. Freeze tests/fixtures/circle_packing/evaluator.py as the verifier artifact. Then run the full search end-to-end with TWO batches:

Batch 1 (c001, c002 in parallel):
  - c001: hexagonal lattice (rows of offset circles, e.g. 6+5+6+5+4=26 or 7+6+7+6=26, varied radius per row)
  - c002: square grid with shrink-to-fit (start uniform, iteratively shrink radii to remove overlaps and maximize sum)

Wait for both to finish, run run_verifier on each, then plan_next(k=2) → start_batch for batch 2.

Batch 2 (c003, c004 in parallel):
  - c003: concentric rings with optimized ring radii (try 1+6+12+7 or 1+8+16+1 type layouts, tune ring radii)
  - c004: boundary-hugging approach (pack circles along the perimeter first, then fill center)

After both batches terminate, run run_verifier on c003 and c004 yourself (no agent_session_id, auto-attribute), then select across all 4 candidates and report.

For each Task: pass only agent_session_id + the one-paragraph directive. Do not hard-code run_id/candidate_id/workspace. Use background: true for each Task (max_parallel=2 > 1).

Report at the end: run_id, all 4 candidate scores + iteration counts, selected candidate_id, and report.md path.
```

### k_module — smoke test, AnySearchAgentFlash

```
Load examples/k_module_search_spec.json. The spec sets max_candidates=2, max_parallel=2, worker_agent_type=AnySearchAgentFlash. Freeze tests/fixtures/k_module_problem/evaluator.py and run end-to-end: freeze_spec → create → plan_next(k=2) → start_batch → start 2 sessions → wait → run_verifier on each → select → report.
```

### signal_processing — multi-batch, AnySearchAgent

```
Load examples/signal_processing_search_spec.json (max_candidates=8, max_parallel=4, AnySearchAgent 50 steps). Freeze tests/fixtures/signal_processing/evaluator.py. Plan + start 4 candidates, supervise through wait_agent_events, then plan + start the next 4 after slots free. Report the best score after both batches.
```

## SWE-bench Style Fixture

`swe_bench_20212_search_spec.json` wraps a SWE-bench bug fix (`sympy__sympy-20212`) instead of a multi-batch optimization. The candidate's job is to patch `evaluate_power` in `tests/fixtures/swe_bench_20212/initial_program.py` so that `evaluate_power(ZERO, NEG_INFINITY)` returns `COMPLEX_INFINITY`. See `tests/fixtures/swe_bench_20212/README.md` for the bug background and the local verification recipe (no sympy or docker required).

```
Load examples/swe_bench_20212_search_spec.json. Freeze tests/fixtures/swe_bench_20212/evaluator.py. Request 4 candidates. After submitting and verifying them, inspect summaries and FAIL_TO_PASS / PASS_TO_PASS results. Stop after report generation and do not promote.
```

Quick local sanity check (no runtime needed):

```bash
cd tests/fixtures/swe_bench_20212 && python3 -c "from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), indent=2))"
```

The buggy baseline returns `combined_score = 0.0`; after applying the two-line gold patch described in the fixture README the score reaches `1.0`.
