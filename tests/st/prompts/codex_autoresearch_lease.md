You are the Codex main agent for the `codex_autoresearch_lease` ST. Use the
project-local Codex `goal-plus` and `search` skills and drive one Search
candidate directly. Do not run pytest, codex, opencode, claude, or any
`tests/st` command. If a required MCP or collaboration tool is unavailable,
emit the final `st_report` with `extra.error`.

Build a SearchSpec from
{{PROJECT_ROOT}}/tests/st/fixtures/k_module_problem/spec.json with these exact
changes before freezing it:

- Set `source_path` to the absolute fixture path.
- Set `budget={"max_candidates": 1, "max_parallel": 1}`.
- Set `strategy.name="random"`.
- Set `strategy.worker_host="codex"`.
- Set `strategy.worker_agent_type="search_candidate_agent"`.
- Set `strategy.worker_budget={"min_runtime_seconds": 300,
  "min_verifier_runs": 1, "max_runtime_seconds": 420,
  "on_exceed": "interrupt"}`.

The 300-second value is a lower-bound AutoResearch lease. The 420-second value
is the separate upper-bound parent watchdog. Require the returned launch to
contain `budget_control.autoresearch_lease.mode="subagent_stop"`,
`initial_wait_timeout_ms=375000`, and `final_wait_timeout_ms=45000`. If these
values are missing or the 300-second minimum is not strictly before the
375-second parent closeout point, fail the scenario instead of launching.

Freeze the fixture evaluator and run this exact flow:

1. `search_freeze_spec` -> `search_create` -> `search_plan_next(k=1)` ->
   `search_start_batch`.
2. Start the candidate agent session with a directive to inspect the current
   program, implement one material variant, call `search_run_verifier` with its
   own `agent_session_id`, write `.tmp/handoff.json`, and deliberately return
   immediately after that first verifier. This early return is intentional: it
   must exercise the `SubagentStop` lease. If the hook blocks the return, the
   same worker must continue useful hypothesis -> implementation -> verifier
   cycles without sleeping or busy-waiting until the lease releases. After
   every additional verifier, immediately attempt to finish again so the hook
   can either continue or release the same child turn.
3. Launch exactly one foreground Codex worker from the returned payload and
   bind its task handle immediately. Do not call `followup_task` or launch a
   replacement worker; hook continuation must keep the original child turn
   alive.
4. Keep the main agent in `wait_agent`. Honor the returned parent watchdog for
   this worker: the initial wait may last 375000 ms. Only after that timeout
   may you send the configured closeout message once, wait the final 45000 ms,
   and interrupt if it is still live. Do not apply a shorter main-agent wait or
   treat the 300-second lower bound as an interrupt deadline.
5. After the worker is terminal, run the main-agent process verifier, then call
   `search_list_iterations`, `search_list_history`, `search_select`, and
   `search_report`. Do not inspect or modify the hook's host-log evidence; the
   outer Python ST assertion owns that evidence.

## ST Output Contract

Output a fenced JSON block tagged `st_report` as the LAST thing in the final
message, with no prose after it:

- scenario: `"codex_autoresearch_lease"`
- run_id: string
- candidates: exactly one
  `{candidate_id, score: number|null, iterations: integer, status: string}`
- selected_candidate_id: string
- best_score: number
- report_path: string
- extra: {
    host: `"codex"`,
    model: `"gpt-5.6-terra"`,
    agent_session_id: string,
    min_runtime_seconds: 300,
    max_runtime_seconds: 420,
    parent_closeout_after_seconds: 375
  }

If the run fails, retain actual partial evidence and put the precise reason in
`extra.error`.
