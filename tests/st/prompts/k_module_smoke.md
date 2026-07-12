Load {{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem/spec.json. The spec sets max_candidates=2, max_parallel=2, worker_agent_type=SearchCandidateAgentFlash. Freeze {{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem/evaluator.py and run end-to-end: freeze_spec → create → plan_next(k=2) → start_batch → start 2 sessions → Task → bind_opencode_session → run_verifier on each → select → report.

## ST Output Contract

When the search is complete, output a fenced JSON block tagged `st_report` as the LAST thing in your final message. No prose after the block. The JSON MUST conform to this schema:

- scenario: "k_module_smoke"
- run_id: string
- candidates: array of { candidate_id, score: number|null, iterations: integer, status: string }
- selected_candidate_id: string | null
- best_score: number | null
- report_path: string
- extra: {}

If the run failed before producing a report, set scenario/run_id as known, leave candidates an empty array, selected_candidate_id null, best_score null, and put the failure reason in extra.error.
