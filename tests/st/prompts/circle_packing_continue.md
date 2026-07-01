Load {{PROJECT_ROOT}}/tests/st/fixtures/circle_packing/spec.json and freeze {{PROJECT_ROOT}}/tests/st/fixtures/circle_packing/evaluator.py.

Run one circle_packing candidate and then continue the same OpenCode session:
  1. freeze_spec → create → plan_next(k=1) → start_batch
  2. call search_start_agent_session for c001
  3. launch Task with session.launch; use a directive like "build a hexagonal or staggered lattice, then tune radii to improve total packed area"
  4. when Task returns, call search_bind_opencode_session(session.agent_session_id, Task metadata.sessionId)
  5. run search_run_verifier(run_id, "c001", "process") from the main agent
  6. call search_continue_agent_session(session.agent_session_id, directive="continue the same circle_packing candidate from the current workspace; tune radii and repair overlaps; do not create a new candidate")
  7. launch Task again with task_id=continued.launch.task_id and the rest of continued.launch
  8. when Task returns, run search_run_verifier(run_id, "c001", "process") again
  9. call search_list_history and search_report

This is the fork-style smoke test for the current implementation: it continues the same OpenCode session with Task task_id instead of using OpenCode Session.fork. Do not create a second agent session for c001.

## ST Output Contract

When the search is complete, output a fenced JSON block tagged `st_report` as the LAST thing in your final message. No prose after the block. The JSON MUST conform to this schema:

- scenario: "circle_packing_continue"
- run_id: string
- candidates: array of { candidate_id, score: number|null, iterations: integer, status: string }
- selected_candidate_id: string | null
- best_score: number | null
- report_path: string
- extra: { agent_session_id: string, opencode_session_id: string, verifier_scores: [number, number], score_delta: number }

If the run failed before producing a report, set scenario/run_id as known, leave candidates an empty array, selected_candidate_id null, best_score null, and put the failure reason in extra.error.
