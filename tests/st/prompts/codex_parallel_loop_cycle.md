You are the Codex main agent for the `codex_parallel_loop_cycle` ST. Use the
project-local Codex `search` skill and drive the Search MCP runtime directly.
Do not run pytest, codex, opencode, claude, or any `tests/st` command. If a
required MCP or collaboration tool is unavailable, emit the final `st_report`
with `extra.error`.

Load {{PROJECT_ROOT}}/tests/st/fixtures/circle_packing/spec.json and make these
exact changes before freezing it:

- `source_path={{PROJECT_ROOT}}/tests/st/fixtures/circle_packing`
- `budget={"max_candidates": 2, "max_parallel": 2}`
- `strategy.name="random"`
- `strategy.orchestration_mode="parallel_loops"`
- `strategy.worker_host="codex"`
- `strategy.worker_agent_type="search_candidate_agent"`
- `strategy.worker_budget={"max_runtime_seconds": 90, "max_turns": 4, "on_exceed": "interrupt"}`
- `strategy.config.seed=42`

The parent and spawned workers use `gpt-5.6-luna`. Always pass `task_name`,
`message`, and `fork_turns` to `spawn_agent`; pass optional launch fields only
when the current collaboration tool schema exposes them.

Freeze the evaluator, create the run, call `search_plan_next(requested_k=2)`
exactly once, and call `search_start_batch` exactly once. Launch and bind both
initial candidates before waiting.

Run one parallel-loop continuation cycle:

1. Use targetless `wait_agent`, then `list_agents`, so either worker can finish
   first. A progress-only wake is not terminal.
2. On the first terminal worker, bind its final handle and run the parent
   completion verifier without `agent_session_id`. Read history/status and
   record the current verifier-backed best candidate and score.
3. If the run is valid and time remains, call
   `search_continue_agent_session` for that exact existing
   `agent_session_id`, using a 90-second dispatch budget and no technical
   direction. Call `followup_task` for the existing task with this neutral
   message:

   Continue the same autonomous search loop from the latest committed evidence.
   Refresh runtime context, choose the next evidence-backed hypothesis yourself,
   verify every material change, and keep working while the assigned budget remains.

4. Never call `search_plan_next` or `search_start_batch` again. Never launch a
   replacement candidate. A low or non-improving score does not alter step 3.
5. Continue wait-any management until the other initial worker and continued
   worker are terminal. Parent-verify every terminal turn.
6. Drain all workers, then call status/history/iterations, select, and report.
7. Read persisted Codex observability for both worker sessions and report every
   distinct observed native worker model. Requested model metadata alone is not
   sufficient evidence.

## ST Output Contract

Output a fenced JSON block tagged `st_report` as the LAST thing in the final
message, with no prose after it:

- scenario: "codex_parallel_loop_cycle"
- run_id: string
- candidates: exactly two ordered objects
  `{ candidate_id, score: number|null, iterations: integer, status: string }`
- selected_candidate_id: string
- best_score: number
- report_path: string
- extra: {
    host: "codex",
    model: "gpt-5.6-luna",
    orchestration_mode: "parallel_loops",
    plans_count: 1,
    initial_agent_session_ids: array of exactly two distinct strings,
    task_names: array of exactly two strings,
    first_completed_candidate_id: string,
    continued_candidate_id: string,
    continued_agent_session_id: string,
    same_worker_continuation: true,
    best_observed_after_first_completion: number,
    best_candidate_observed_after_first_completion: string,
    new_candidates_after_initial: 0,
    observed_worker_models: ["gpt-5.6-luna"]
  }

If the run fails, retain actual partial evidence and put the precise reason in
`extra.error`.
