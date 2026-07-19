# Examples

The checked-in examples use the maintained parallel-loop contract. A Search
run creates one initial plan, materializes a fixed set of candidate workspaces,
and resumes those same candidates until the global stop condition is true.

## SearchSpec Files

| Spec | Source | Purpose |
|---|---|---|
| `circle_packing_search_spec.json` | `tests/fixtures/circle_packing` | numeric optimization with two lanes |
| `k_module_search_spec.json` | `tests/fixtures/k_module_problem` | small multi-file correctness and score fixture |
| `signal_processing_search_spec.json` | `tests/fixtures/signal_processing` | wider parallel score fixture |
| `edgebench_ad_placement_search_spec.json` | `examples/edgebench-ad-placement/workspace` | public-score C++ workflow |
| `swe_bench_20212_search_spec.json` | `tests/fixtures/swe_bench_20212` | repository patching fixture |
| `workspace-backends/git_worktree_search_spec.json` | `examples/workspace-backends/source` | Git worktree materialization demo |

Paths in specs are repository-relative so tests and host agents can load them
without machine-specific configuration.

## Strategy Contract

Maintained hosts are Codex and Pi RPC. Use one of these strategy shapes:

```json
{
  "strategy": {
    "name": "random",
    "orchestration_mode": "parallel_loops",
    "worker_host": "codex"
  }
}
```

```json
{
  "strategy": {
    "name": "agent_guided",
    "orchestration_mode": "parallel_loops",
    "worker_host": "pi-rpc",
    "worker_budget": {
      "max_runtime_seconds": 600,
      "max_turns": 8,
      "on_exceed": "interrupt"
    }
  }
}
```

`random` creates fixed independent source branches. `agent_guided` requires
the main agent to provide the initial lane intents to `search_start_batch`.
Neither strategy permits a second `search_plan_next` call.

Set `budget.max_candidates` equal to `budget.max_parallel` for normal runs.
Each candidate workspace is a long-lived lane, not a disposable attempt.

## Host Flow

Codex launches every initial lane using `search_start_agent_session`,
`spawn_agent`, and `search_bind_agent_handle`. When a worker returns and the
global stop condition is false, continue the same native subagent with
`search_continue_agent_session` and `followup_task`.

Pi opens the fixed lane set with `pi_search_pool_open`, consumes terminal
events with `pi_search_pool_wait_any`, and resumes the same candidate/native
session with `pi_search_pool_continue`. `pi_search_pool_snapshot` recovers pool
state after a main-session interruption; `pi_search_pool_close` drains or
interrupts it before final selection. There is no public synchronous batch,
single-candidate runner, or manual pool submit API.

For both hosts:

1. Freeze the verifier-backed spec and create the run.
2. Call `search_plan_next` exactly once.
3. Materialize all initial candidates with `search_start_batch`.
4. Let every lane own its internal hypothesis, edits, verifier calls, and
   evidence commits.
5. Resume completed lanes while time remains; do not create replacement
   candidates based on score or rank.
6. Drain workers, call `search_select`, record/promote the result, finish the
   Goal Plus audit, then call `search_report` after terminal status.

## Workspace Backends

`workspace.backend="copy"` is useful for simple fixtures. The default
`git_worktree` backend creates one branch/worktree per initial lane and keeps
verifier-backed Git revisions available for final selection.

Run the host-free worktree demo with:

```bash
python examples/workspace-backends/run_demo.py --runtime-root .tmp/worktree-demo
```

## Scenario Guides

- [AscendC Direct Search](ascendc-direct-search/README.md)
- [CANNBench TileLang Ascend](cannbench-tilelang-ascend/README.md)
- [EdgeBench Ad Placement](edgebench-ad-placement/README.md)
- [Goal and Goal Plus](goal-and-goal-plus/README.md)
- [Kernel Optimize](kernel-optimize/README.md)
- [Model Optimize](model-optimize/README.md)
- [Model Optimize GPU (WIP)](model-opt-gpu/README.md)
- [Git Worktree Backend](workspace-backends/README.md)

The GPU model-optimization example is explicitly WIP and is not part of the
maintained validation gate.

## Hidden-Answer QA

Do not expose hidden correctness labels through a worker-visible verifier.
Use only format validation during Search, then grade finalized answers in a
parent evaluator with a predeclared gold-independent aggregation rule.
