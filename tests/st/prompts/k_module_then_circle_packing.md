Run TWO independent search runs back-to-back, then report both. This tests
that one search does not bleed into the next: each run gets its own frozen
spec, run_id, and candidate workspace, and the second run starts from a clean
runtime slate.

## Run 1: k_module smoke

Load {{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem/spec.json. The spec
sets max_candidates=2, max_parallel=2, worker_agent_type=SearchCandidateAgentFlash.
Freeze {{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem/evaluator.py and
run end-to-end: freeze_spec → create → plan_next(k=2) → start_batch → start
2 sessions → Task → bind_opencode_session → run_verifier on each → select →
report.

Record the first run_id as RUN_1.

## Run 2: circle_packing two-batch

Load {{PROJECT_ROOT}}/tests/st/fixtures/circle_packing/spec.json. The spec
sets max_candidates=4, max_parallel=2, worker_agent_type=SearchCandidateAgentFlash.
Freeze {{PROJECT_ROOT}}/tests/st/fixtures/circle_packing/evaluator.py as the
verifier artifact. Then run the full search end-to-end with TWO batches:

Batch 1 (c001, c002):
  - c001: hexagonal lattice (rows of offset circles, e.g. 6+5+6+5+4=26 or 7+6+7+6=26, varied radius per row)
  - c002: square grid with shrink-to-fit (start uniform, iteratively shrink radii to remove overlaps and maximize sum)

Wait for both to finish, run run_verifier on each, then plan_next(k=2) →
start_batch for batch 2.

Batch 2 (c003, c004):
  - c003: concentric rings with optimized ring radii (try 1+6+12+7 or 1+8+16+1 type layouts, tune ring radii)
  - c004: boundary-hugging approach (pack circles along the perimeter first, then fill center)

After both batches terminate, run run_verifier on c003 and c004 yourself (no
agent_session_id, auto-attribute), then select across all 4 candidates and
report.

Record the second run_id as RUN_2.

## Isolation check

Confirm RUN_1 != RUN_2. The two run_ids must be distinct strings; if they
collide, the runtime is leaking state across runs and the ST should fail.

For each Task in both runs: use the runtime launch payload, then bind the
returned Task metadata.sessionId. Do not hard-code run_id/candidate_id/workspace.

## ST Output Contract

When both searches are complete, output a fenced JSON block tagged
`st_report` as the LAST thing in your final message. No prose after the
block. The JSON MUST conform to this schema:

- scenario: "k_module_then_circle_packing"
- run_id: string              # RUN_2 (the last run; for legacy compatibility)
- candidates: array of { candidate_id: string, score: number|null, iterations: integer, status: string }   # from RUN_2
- selected_candidate_id: string | null     # from RUN_2
- best_score: number | null                 # from RUN_2
- report_path: string                       # from RUN_2
- extra: {
    run1_run_id: string,      # RUN_1
    run1_candidates: integer, # count of evaluated candidates in RUN_1
    run1_best_score: number|null,
    run2_run_id: string,      # RUN_2 (same as top-level run_id)
    run2_candidates: integer,
    run2_best_score: number|null,
    run_ids_distinct: boolean # must be true
  }

If either run failed before producing a report, set scenario/run_id as known,
leave candidates an empty array, selected_candidate_id null, best_score null,
and put the failure reason in extra.error (which run failed, and why).
