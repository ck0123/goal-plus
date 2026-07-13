You are the Codex main agent for the `codex_time_advisory` ST. Use the Codex
`goal-plus` and `search` skills, but drive one small Search Mode candidate
directly once the spec is clear. Do not run pytest, codex, opencode, claude, or
any `tests/st` command. If the required MCP or foreground-worker tools are
unavailable, emit the final `st_report` with `extra.error`.

Build a SearchSpec from
{{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem/spec.json with these edits:

- Set `source_path` to the absolute fixture path.
- Set `budget.max_candidates=1` and `budget.max_parallel=1`.
- Set `strategy.name="random"`.
- Set `strategy.worker_host="codex"`.
- Set `strategy.worker_agent_type="search_candidate_agent"`.
- Set `strategy.worker_budget={"max_runtime_seconds": 120, "max_turns": 6, "on_exceed": "interrupt"}`.

Freeze the fixture evaluator and run:

1. `search_freeze_spec` -> `search_create` -> `search_plan_next(k=1)` ->
   `search_start_batch`.
2. Start the candidate agent session and launch its foreground Codex worker
   from the returned launch payload. Tell the worker to edit
   `initial_program.py`, run `search_run_verifier` with its own
   `agent_session_id`, then read the final artifact once more before returning.
3. Honor the returned parent-watchdog wait/closeout/interrupt sequence.
4. Bind the returned task handle, run the main-agent verifier, then call
   `search_list_iterations`, `search_select`, and `search_report`.

The test harness supplies an already-expired informational outer deadline.
The worker must still complete normally; the host hook should inject a
PostTool time advisory after verifier evidence exists. Do not inspect or write
the hook's host-log evidence yourself.

## ST Output Contract

Output a fenced JSON block tagged `st_report` as the LAST thing in the final
message, with no prose after it:

- scenario: `"codex_time_advisory"`
- run_id: string
- candidates: array of `{candidate_id, score, iterations, status}`
- selected_candidate_id: string or null
- best_score: number or null
- report_path: string
- extra: `{host: "codex", model: "gpt-5.6-terra", agent_session_id: string}`

If the run fails, leave candidates empty and put the reason in `extra.error`.
