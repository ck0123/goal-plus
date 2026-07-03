# OpenEvolve Strategy

This repo implements a basic OpenEvolve-style planner as a builtin strategy:

```json
{"strategy": {"name": "openevolve", "driver": "builtin"}}
```

## Method Logic

This is the minimal OpenEvolve core that fits the MCP-first runtime:

1. Bootstrap from source while no verified programs exist.
2. Treat scored candidates as the program database.
3. Build an archive from top-scoring candidates.
4. Sample a parent using OpenEvolve-style ratios:
   - `exploration_ratio`: sample from the scored population;
   - `exploitation_ratio`: sample from the elite archive;
   - remaining probability: random population fallback.
5. Sample inspirations:
   - include the best program if it is not already the parent;
   - include archive programs;
   - fill remaining slots from random scored candidates.
6. Emit fixed `work_orders` that copy the sampled parent workspace and ask the worker for a compact mutation.

It intentionally does not implement the full OpenEvolve database stack: persistent population storage, MAP-Elites feature cells, island migration, novelty judging, and prompt templating remain outside this minimal MCP runtime. The runtime keeps the search-control boundary: it plans lineage and context, while OpenCode Task workers do the actual edit/verify loop.

## Code Path

```text
examples/search-mode/k_module_openevolve_search_spec.json
  strategy.name = "openevolve"
  strategy.driver = "builtin"

FileSearchRuntime.plan_next
  -> _plan_openevolve
  -> _openevolve_archive
  -> _openevolve_sample_parent
  -> _openevolve_sample_inspirations
  -> writes plan_XXX.json

FileSearchRuntime.start_batch
  -> _proposal_from_work_order
  -> _create_candidate_task
  -> copies parent workspace into the new candidate workspace

FileSearchRuntime.start_agent_session
  -> _build_launch_payload
  -> OpenCode Task worker

FileSearchRuntime.run_verifier
  -> records ScoreReport and IterationRecord
  -> the next openevolve plan samples from updated history
```

## Config

```json
{
  "strategy": {
    "name": "openevolve",
    "config": {
      "seed": 1,
      "archive_size": 5,
      "num_inspirations": 2,
      "exploration_ratio": 0.2,
      "exploitation_ratio": 0.7,
      "elite_selection_ratio": 0.1
    }
  }
}
```

`elite_selection_ratio` is documented for parity with OpenEvolve configs; the current minimal runtime uses `archive_size` directly for archive construction.

