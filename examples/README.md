# Search Examples

The example specs are small local scenarios for exercising the Search MCP runtime.

| Spec | Fixture | Purpose |
|---|---|---|
| `k_module_search_spec.json` | `tests/fixtures/k_module_problem` | Single-round control-plane smoke test with four discrete configuration slots. |
| `circle_packing_search_spec.json` | `tests/fixtures/circle_packing` | Multi-batch geometric optimization for circle packing. |
| `signal_processing_search_spec.json` | `tests/fixtures/signal_processing` | Multi-batch algorithm search for causal signal filtering. |

For the multi-batch examples, create the run, call `search_plan_next(run_id, 4)`, then start the returned plan with `search_start_batch(run_id, plan_id)`. Submit and verify those candidates, inspect their artifacts and verifier results, then optionally plan another batch. The compatibility helper `search_next_batch(run_id, 4)` still works for the default fixed-work-order examples. The runtime enforces isolated workspaces and verifier-owned scoring; the active strategy defines how later candidates should derive from history.

Before requesting a follow-up batch, the host can call `search_list_history(run_id)` to recover a compact JSON summary of the best candidates so far. To advance the search, call `search_plan_next`; the returned plan states the current strategy mode, worker policy, official history view, derivation policy, and whether the host must submit proposals.

`strategy.worker_mode` controls candidate execution:

- `main-agent-search-direct`: the main agent edits candidate workspaces directly.
- `sub-agent-search-dispatch`: call `search_prepare_worker(run_id, candidate_id, main_directive, timeout_seconds?)` first. `main_directive` may be a plain string or a structured object. If `worker_agent_type` is set, use it as the OpenCode `subagent_type`; bundled dispatch examples use `AnySearchAgent`. Pass the returned `dispatch_id` to the worker and have the worker call `search_get_worker_context(dispatch_id)` before editing. Candidate submission must include `dispatch_id` and `context_hash`.

`strategy.worker_timeout_seconds` is the default per-candidate worker timebox. It defaults to 600 seconds, and `search_prepare_worker(..., timeout_seconds=...)` may override one dispatch. Hosts should collect best-so-far artifacts by the timeout and then run runtime-owned verification.

`strategy.worker_local_verifier_max_runs` limits worker-local verifier/scorer calls during candidate exploration. It defaults to 0, so workers do not run actual scoring/evaluator commands; runtime-owned verification after submission is required. Workers may run non-scoring static checks such as `py_compile`.

## Budget Semantics

Each `SearchSpec` must include an explicit `budget`; there are no runtime defaults.

```json
{
  "budget": {
    "max_candidates": 8,
    "max_parallel": 4,
    "wall_clock_seconds": 600
  }
}
```

- `max_candidates`: total candidate workspaces allowed for the run. The runtime enforces this when `search_plan_next`, `search_start_batch`, or `search_next_batch` is called.
- `max_parallel`: intended host-side concurrency. In these examples, the host should request candidates in batches of up to 4.
- `wall_clock_seconds`: run-level time budget recorded in the spec. V0 does not yet stop a run automatically when this expires.
- `max_worker_seconds` and `max_tokens`: optional worker-level hints. V0 includes `max_worker_seconds` in candidate task stop conditions but does not independently enforce host token usage.

Freeze the matching evaluator as the verifier artifact:

```text
tests/fixtures/circle_packing/evaluator.py
tests/fixtures/signal_processing/evaluator.py
```

The specs intentionally do not prescribe what each worker must try; workers should submit their result and a useful summary of what they actually changed. Candidate dependencies are part of the verifier environment contract: if a candidate uses a package that is unavailable, the runtime verifier should record that failure instead of changing the task framing.

## Strategy Modes

Example specs currently use the default `independent_branches` strategy. To test strategy-aware follow-up behavior, add a structured strategy block to a copied spec:

```json
{
  "strategy": {
    "name": "agent_guided",
    "driver": "builtin",
    "worker_mode": "sub-agent-search-dispatch",
    "worker_agent_type": "AnySearchAgent",
    "worker_timeout_seconds": 600,
    "worker_local_verifier_max_runs": 0,
    "history_policy": {"scope": "top_n", "top_n": 5}
  }
}
```

In `agent_guided`, `search_plan_next` returns a proposal contract and `search_start_batch` must receive explicit proposals. In `evolve`, the runtime selects a parent and inspirations, then starts candidate workspaces from the selected parent.

## OpenCode Commands

From the project root, run the circle packing example with:

```bash
opencode run --command search "Run the circle_packing search. Use examples/circle_packing_search_spec.json and freeze tests/fixtures/circle_packing/evaluator.py. Start by requesting 4 candidates. After submitting and verifying them, inspect candidate summaries and verifier scores, then request up to 4 more candidates if useful. Stop after report generation and do not promote."
```

Run the signal processing example with:

```bash
opencode run --command search "Run the signal_processing search. Use examples/signal_processing_search_spec.json and freeze tests/fixtures/signal_processing/evaluator.py. Start by requesting 4 candidates. After submitting and verifying them, inspect candidate summaries and verifier scores, then request up to 4 more candidates if useful. Stop after report generation and do not promote."
```

For the OpenCode TUI, start `opencode` and send the same text prefixed by `/search`.
