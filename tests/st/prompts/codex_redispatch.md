You are the Codex main agent for the `codex_redispatch` ST. Use the Codex
`goal-plus` and `search` skills, but drive Search Mode directly once the spec
is clear. Do not run pytest, codex, opencode, claude, or any `tests/st`
command; doing so would recursively start this same ST instead of testing the
runtime. If the required MCP or foreground-worker tools are unavailable, emit
the final `st_report` with `extra.error` instead of launching another test.
Use `gpt-5.6-terra` as the model value in `extra.model`.

Build a SearchSpec from {{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem/spec.json
with these required edits before freezing:

- Set `source_path` to the absolute path
  `{{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem`.
- Set `budget.max_candidates=1` and `budget.max_parallel=1`.
- Set `strategy.name="random"`.
- Set `strategy.worker_host="codex"`.
- Set `strategy.worker_agent_type="search_candidate_agent"`.
- Set `strategy.worker_budget={"max_runtime_seconds": 90, "max_turns": 4, "on_exceed": "interrupt"}`.

Freeze {{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem/evaluator.py, then
run this exact control-flow:

1. `search_freeze_spec` -> `search_create` -> `search_plan_next(k=1)` -> `search_start_batch`.
2. Call `search_start_agent_session` for the single candidate.
3. Launch the returned Codex foreground worker with `spawn_agent` using the
   returned launch payload. If `launch.budget_control.mode == "parent_watchdog"`,
   first use `wait_agent(timeout_ms=launch.budget_control.initial_wait_timeout_ms)`.
   On timeout, send `launch.budget_control.closeout_message` to
   `launch.budget_control.closeout_target`, wait once more for
   `launch.budget_control.final_wait_timeout_ms`, and only then interrupt if it
   still has not returned.
4. Bind any returned Codex task name or nickname with `search_bind_agent_handle`.
5. Run `search_run_verifier(run_id, candidate_id, "process")` from the main agent.
6. Call `search_redispatch_candidate` for the same `candidate_id` with:
   `worker_agent_type="search_candidate_agent"` and
   `worker_budget={"max_runtime_seconds": 180, "max_turns": 8, "on_exceed": "interrupt"}`.
7. Confirm the redispatch response has a second, different `agent_session_id`,
   the same `candidate_id`, and `launch.budget_control.mode == "parent_watchdog"`.
8. Launch the redispatched Codex foreground worker with the returned launch
   payload, bind any returned handle, and wait the same way as above.
9. Run `search_run_verifier(run_id, candidate_id, "process")` again.
10. Call `search_list_iterations`, `search_list_history`, `search_select`, and
    `search_report`.

The important behavior under test is state-level resume for Codex: there must
be two different agent_session_id values for the same candidate, and the second
session must come from `search_redispatch_candidate`.

## ST Output Contract

When the search is complete, output a fenced JSON block tagged `st_report` as the LAST thing in your final message. No prose after the block. The JSON MUST conform to this schema:

- scenario: "codex_redispatch"
- run_id: string
- candidates: array of { candidate_id, score: number|null, iterations: integer, status: string }
- selected_candidate_id: string | null
- best_score: number | null
- report_path: string
- extra: {
    host: "codex",
    model: "gpt-5.6-terra",
    candidate_id: string,
    first_agent_session_id: string,
    redispatch_agent_session_id: string,
    same_candidate: boolean,
    redispatch_budget_control_mode: "parent_watchdog",
    task_names: array<string>,
    verifier_scores: array<number|null>
  }

If the run failed before producing a report, set scenario/run_id as known, leave candidates an empty array, selected_candidate_id null, best_score null, and put the failure reason in extra.error.
