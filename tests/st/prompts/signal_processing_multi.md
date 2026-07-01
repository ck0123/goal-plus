Load {{PROJECT_ROOT}}/tests/st/fixtures/signal_processing/spec.json (max_candidates=8, max_parallel=4, AnySearchAgent 50 steps). Freeze {{PROJECT_ROOT}}/tests/st/fixtures/signal_processing/evaluator.py. Plan + start 4 candidates, wait for each OpenCode Task to return, then plan + start the next 4. Report the best score after both batches.

## ST Output Contract

When the search is complete, output a fenced JSON block tagged `st_report` as the LAST thing in your final message. No prose after the block. The JSON MUST conform to this schema:

- scenario: "signal_processing_multi"
- run_id: string
- candidates: array of { candidate_id, score: number|null, iterations: integer, status: string }
- selected_candidate_id: string | null
- best_score: number | null
- report_path: string
- extra: { batches: integer }

If the run failed before producing a report, set scenario/run_id as known, leave candidates an empty array, selected_candidate_id null, best_score null, and put the failure reason in extra.error.
