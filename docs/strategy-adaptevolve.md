# AdaptEvolve Strategy

This repo implements AdaptEvolve as a Python strategy plugin:

```text
goal_plus.strategies.adaptevolve:AdaptEvolveStrategy
```

## Method Logic

AdaptEvolve is not `AdaptiveSearch + Evolve`. The useful part for this runtime is adaptive compute allocation inside an evolve-style search loop:

1. Use evolve-style parent selection:
   - first batch has no scored parent, so it bootstraps from source;
   - later batches pick the best verified candidate as parent;
   - top alternatives are exposed as inspirations.
2. Use MCP-observable confidence proxies to choose worker tier:
   - no scored candidates -> `SearchCandidateAgentFlash`;
   - low score or process failure -> `SearchCandidateAgentDeep`;
   - repeated failures -> `SearchCandidateAgentExtraDeep`;
   - high score -> `SearchCandidateAgentFlash`;
   - medium confidence -> `SearchCandidateAgent`.
3. Emit fixed `work_orders` plus a plan-level `worker_policy`.

The original paper-style signal would be model-token confidence. This runtime does not own model logits, so the implemented proxy is deliberately conservative: score, process pass/fail, failure classes, and history availability.

## Code Path

```text
examples/search-mode/k_module_adaptevolve_search_spec.json
  strategy.driver = "python"
  strategy.ref = "goal_plus.strategies.adaptevolve:AdaptEvolveStrategy"

FileSearchRuntime.plan_next
  -> _plan_custom_strategy
  -> _plan_python_strategy
  -> AdaptEvolveStrategy.plan_next(payload)
  -> _normalize_worker_policy(...)
  -> writes plan_XXX.json

FileSearchRuntime.start_batch
  -> _proposal_from_work_order
  -> _create_candidate_task
  -> task.strategy_metadata["worker_policy"]

FileSearchRuntime.start_agent_session
  -> _build_launch_payload
  -> _candidate_worker_agent_type
  -> session.launch.subagent_type

OpenCode Task
  -> launches SearchCandidateAgentFlash / SearchCandidateAgent / SearchCandidateAgentDeep / SearchCandidateAgentExtraDeep
  -> worker calls search_get_agent_context and search_run_verifier

FileSearchRuntime.run_verifier
  -> records ScoreReport and IterationRecord
  -> next plan_next sees updated history
```

## MCP Boundary

This stays within the repo's MCP principle:

- MCP runtime is the control plane and blackboard.
- Strategy plugin only plans parent choice, derivation policy, and worker tier.
- Runtime does not supervise, wait, abort, or observe worker lifecycle.
- OpenCode owns Task execution; `session.launch` is the only authoritative dispatch payload.
- Candidate workers still self-direct and self-verify through MCP; AdaptEvolve only changes the starting worker tier and lineage.
