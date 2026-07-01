Load a copy of {{PROJECT_ROOT}}/examples/circle_packing_search_spec.json with strategy.name set to "random" (keep max_candidates=4, max_parallel=2, worker_agent_type=AnySearchAgentFlash; optionally set strategy.config.seed=42 for a reproducible parent pick). Freeze {{PROJECT_ROOT}}/tests/fixtures/circle_packing/evaluator.py as the verifier artifact. Then run the full search end-to-end with TWO batches:

Batch 1 (c001, c002 — random bootstrap, both derive from source):
  - c001: hexagonal lattice (rows of offset circles, e.g. 6+5+6+5+4=26 or 7+6+7+6=26, varied radius per row)
  - c002: square grid with shrink-to-fit (start uniform, iteratively shrink radii to remove overlaps and maximize sum)

Wait for both to finish, run run_verifier on each, then plan_next(k=2) → start_batch for batch 2. The runtime will randomly pick one of {c001, c002} as the parent; batch 2 workspaces are copied from that parent.

Batch 2 (c003, c004 — both mutate the runtime-picked parent):
  - c003: concentric rings with optimized ring radii (try 1+6+12+7 or 1+8+16+1 type layouts, tune ring radii)
  - c004: boundary-hugging approach (pack circles along the perimeter first, then fill center)

After both batches terminate, run run_verifier on c003 and c004 yourself (no agent_session_id, auto-attribute), then select across all 4 candidates and report. Report strategy_trace.parent_candidate_id from the batch 2 plan so the random pick is visible.

For each Task: use the runtime launch payload, then bind the returned Task metadata.sessionId. Do not hard-code run_id/candidate_id/workspace.

## ST Output Contract

When the search is complete, output a fenced JSON block tagged `st_report` as the LAST thing in your final message. No prose after the block. The JSON MUST conform to this schema:

- scenario: "circle_packing_random"
- run_id: string
- candidates: array of { candidate_id, score: number|null, iterations: integer, status: string }
- selected_candidate_id: string | null
- best_score: number | null
- report_path: string
- extra: { parent_candidate_id: string }

If the run failed before producing a report, set scenario/run_id as known, leave candidates an empty array, selected_candidate_id null, best_score null, and put the failure reason in extra.error.
