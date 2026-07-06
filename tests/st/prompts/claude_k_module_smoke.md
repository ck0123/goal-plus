Use the Claude Code `goal-plus` and `search` skills, but drive Search Mode
directly once the spec is clear.

Build a SearchSpec from {{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem/spec.json
with these required edits before freezing:

- Set `source_path` to the absolute path
  `{{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem`.
- Set `budget.max_candidates=1` and `budget.max_parallel=1`.
- Set `strategy.name="random"`.
- Set `strategy.worker_host="claude-code"`.
- Set `strategy.worker_agent_type="any-search-agent-flash"`.
- Set `strategy.worker_budget={"max_turns": 4, "on_exceed": "interrupt"}`.

Freeze {{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem/evaluator.py, then
run end-to-end: freeze_spec -> create -> plan_next(k=1) -> start_batch ->
start_agent_session -> foreground Agent launch -> bind_agent_handle if the
Agent result includes a reusable id/name -> run_verifier -> select -> report.

## ST Output Contract

When the search is complete, output a fenced JSON block tagged `st_report` as the LAST thing in your final message. No prose after the block. The JSON MUST conform to this schema:

- scenario: "claude_k_module_smoke"
- run_id: string
- candidates: array of { candidate_id, score: number|null, iterations: integer, status: string }
- selected_candidate_id: string | null
- best_score: number | null
- report_path: string
- extra: { host: "claude-code", agent_session_id: string|null }

If the run failed before producing a report, set scenario/run_id as known, leave candidates an empty array, selected_candidate_id null, best_score null, and put the failure reason in extra.error.
