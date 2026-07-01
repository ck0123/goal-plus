Load {{PROJECT_ROOT}}/examples/swe_bench_20212_search_spec.json. Freeze {{PROJECT_ROOT}}/tests/fixtures/swe_bench_20212/evaluator.py. Request 4 candidates. After submitting and verifying them, inspect summaries and FAIL_TO_PASS / PASS_TO_PASS results. Stop after report generation and do not promote.

## ST Output Contract

When the search is complete, output a fenced JSON block tagged `st_report` as the LAST thing in your final message. No prose after the block. The JSON MUST conform to this schema:

- scenario: "swe_bench_20212"
- run_id: string
- candidates: array of { candidate_id, score: number|null, iterations: integer, status: string }
- selected_candidate_id: string | null
- best_score: number | null
- report_path: string
- extra: { fail_to_pass: array<string>, pass_to_pass: array<string> }

If the run failed before producing a report, set scenario/run_id as known, leave candidates an empty array, selected_candidate_id null, best_score null, and put the failure reason in extra.error.
