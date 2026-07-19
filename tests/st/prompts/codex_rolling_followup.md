You are the Codex main agent for the `codex_rolling_followup` ST. Use the
project-local Codex `search` skill and drive the Search MCP runtime directly.
Do not run pytest, codex, opencode, claude, or any `tests/st` command. If a
required MCP or collaboration tool is unavailable, emit the final `st_report`
with `extra.error`.

Load {{PROJECT_ROOT}}/tests/st/fixtures/circle_packing/spec.json and make these
exact changes before freezing it:

- `source_path={{PROJECT_ROOT}}/tests/st/fixtures/circle_packing`
- `budget={"max_candidates": 2, "max_parallel": 2}`
- `strategy.name="random"`
- `strategy.worker_host="codex"`
- `strategy.worker_agent_type="search_candidate_agent"`
- `strategy.worker_budget={"max_runtime_seconds": 90, "max_turns": 4, "on_exceed": "interrupt"}`
- `strategy.config.seed=42`

The parent and spawned workers use `gpt-5.6-terra`. The current Codex schema
may hide optional launch metadata, so always pass `task_name`, `message`, and
`fork_turns` to `spawn_agent`; pass optional fields only when exposed.

Freeze the fixture evaluator, create the run, plan two candidates, and start the
batch. Start one agent session for each candidate with distinct macro
directions, then launch and bind both workers before waiting.

Manage them as parallel persistent loops:

1. Call targetless `wait_agent` so any worker can wake the main agent. After
   every wake, call `list_agents`; a progress-only wake is not terminal.
2. Enforce each launch payload's watchdog independently. Send its configured
   closeout message once at soft deadline and interrupt only that worker at its
   hard deadline.
3. When the first worker becomes terminal, immediately bind final metadata and
   call the main-agent `search_run_verifier` for that candidate. Do not wait for
   the other initial worker merely because both came from one Search plan.
4. Resume that first completed loop: call `search_continue_agent_session` with
   the same `agent_session_id` and
   `worker_budget={"max_runtime_seconds": 180, "max_turns": 8, "on_exceed": "interrupt"}`.
   Do not provide a new technical directive; the returned neutral continuation
   tells the worker to choose its next action from current evidence.
   Require `launch.tool="followup_task"`, then call `followup_task` with the
   returned target/message. This must reuse the original Codex worker, runtime
   agent session, candidate, and workspace.
5. Continue targetless wait-any management until the other initial worker and
   the continued worker are terminal. Run a main-agent process verifier after
   each terminal turn. Do not create another candidate or agent session.
6. Drain all live workers, then call history, iterations, status, select, and
   report.

## ST Output Contract

Output a fenced JSON block tagged `st_report` as the LAST thing in the final
message, with no prose after it:

- scenario: "codex_rolling_followup"
- run_id: string
- candidates: exactly two ordered objects
  `{ candidate_id, score: number|null, iterations: integer, status: string }`
- selected_candidate_id: string
- best_score: number
- report_path: string
- extra: {
    host: "codex",
    model: "gpt-5.6-terra",
    wait_mode: "wait_any",
    initial_agent_session_ids: array of exactly two distinct strings,
    task_names: array of exactly two strings,
    first_completed_candidate_id: string,
    continued_candidate_id: string,
    continued_agent_session_id: string,
    continue_tool: "followup_task",
    same_worker_continuation: true
  }

If the run fails, retain actual partial evidence and put the precise reason in
`extra.error`.
