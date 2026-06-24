# Search Examples

The example specs are small local scenarios for exercising the Search MCP runtime.

| Spec | Fixture | Purpose |
|---|---|---|
| `k_module_search_spec.json` | `tests/fixtures/k_module_problem` | Single-round control-plane smoke test with four discrete configuration slots. |
| `circle_packing_search_spec.json` | `tests/fixtures/circle_packing` | Multi-batch geometric optimization for circle packing. |
| `signal_processing_search_spec.json` | `tests/fixtures/signal_processing` | Multi-batch algorithm search for causal signal filtering. |

For the multi-batch examples, create the run, call `search_next_batch(run_id, 4)`, submit and verify those candidates, inspect their artifacts and verifier results, then optionally call `search_next_batch(run_id, 4)` again. The runtime enforces isolated workspaces and verifier-owned scoring; the host agent decides what follow-up work to request from later candidates.

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

- `max_candidates`: total candidate workspaces allowed for the run. The runtime enforces this when `search_next_batch` is called.
- `max_parallel`: intended host-side concurrency. In these examples, the host should request candidates in batches of up to 4.
- `wall_clock_seconds`: run-level time budget recorded in the spec. V0 does not yet stop a run automatically when this expires.
- `max_worker_seconds` and `max_tokens`: optional worker-level hints. V0 includes `max_worker_seconds` in candidate task stop conditions but does not independently enforce host token usage.

Freeze the matching evaluator as the verifier artifact:

```text
tests/fixtures/circle_packing/evaluator.py
tests/fixtures/signal_processing/evaluator.py
```

The fixtures are dependency-light and run with the default development install. The specs intentionally do not prescribe what each worker must try; workers should submit their result and a useful summary of what they actually changed.

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
