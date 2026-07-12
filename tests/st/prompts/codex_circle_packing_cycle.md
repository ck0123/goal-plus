You are the Codex main agent for the `codex_circle_packing_cycle` ST. Use the
project-local Codex `search` skill and drive the Search MCP runtime directly.
Do not run pytest, codex, opencode, claude, or any `tests/st` command; doing so
would recursively launch this same ST. If a required MCP or collaboration tool
is unavailable, emit the final `st_report` with `extra.error`.

Load {{PROJECT_ROOT}}/tests/st/fixtures/circle_packing/spec.json and make these
exact changes before freezing it:

- Set `source_path` to the absolute path
  `{{PROJECT_ROOT}}/tests/st/fixtures/circle_packing`.
- Set `budget` to `{"max_candidates": 4, "max_parallel": 2}`.
- Set `strategy.name="random"` and `strategy.driver="builtin"`.
- Set `strategy.worker_mode="agent-session-pool"`.
- Set `strategy.worker_host="codex"`.
- Set `strategy.worker_agent_type="any_search_agent"`.
- Set `strategy.worker_budget={"max_runtime_seconds": 180, "max_turns": 8, "on_exceed": "interrupt"}`.
- Set `strategy.config.seed=42`.

The parent ST runner uses `gpt-5.6-terra`; each spawned worker inherits the parent Codex model.
The current Codex collaboration schema intentionally hides
optional launch metadata. For this run, project every runtime launch payload
onto that schema: pass only `task_name`, `message`, and `fork_turns` to
`spawn_agent`. Do not pass `agent_type`, `model`, `reasoning_effort`, or
`service_tier`, and do not treat their omission as an error.

Freeze {{PROJECT_ROOT}}/tests/st/fixtures/circle_packing/evaluator.py, then call
`search_create`. Execute exactly two rounds with batch size two.

## Round 1

1. Call `search_plan_next(k=2)` and `search_start_batch`; require exactly
   candidates `c001` and `c002`.
2. Start one agent session for each candidate with distinct directives:
   - `c001`: implement a hexagonal lattice for 26 circles.
   - `c002`: implement a square-grid shrink-to-fit packing for 26 circles.
3. Launch both returned Codex payloads with `spawn_agent` before waiting for
   either worker, using the schema projection described above.
4. Immediately bind each returned task name/nickname with
   `search_bind_agent_handle`.
5. For each live task, apply its `parent_watchdog`: wait for
   `initial_wait_timeout_ms`; on timeout send exactly one `closeout_message`,
   wait for `final_wait_timeout_ms`, then interrupt only after a second timeout.
6. Run a main-agent `search_run_verifier(..., phase="process")` for both
   candidates after the workers return or are interrupted.

## Round 2

1. Call `search_plan_next(k=2)` and `search_start_batch`; require exactly
   candidates `c003` and `c004`.
2. Start one agent session for each candidate with distinct directives:
   - `c003`: implement concentric rings with tuned ring radii.
   - `c004`: implement a boundary-hugging layout followed by center filling.
3. Again launch both workers before waiting, bind both handles, apply the same
   two-stage watchdog, and run a main-agent process verifier for both.

After both rounds, call `search_list_iterations`, `search_list_history`,
`search_status`, `search_select`, and `search_report`. Do not report success
unless there are exactly four evaluated candidates, each with at least one
verifier iteration, and four distinct `agent_session_id` values.

## ST Output Contract

Output a fenced JSON block tagged `st_report` as the LAST thing in the final
message, with no prose after it. The JSON MUST conform to this schema:

- scenario: "codex_circle_packing_cycle"
- run_id: string
- candidates: array of exactly four
  `{ candidate_id, score: number|null, iterations: integer, status: string }`
  objects ordered `c001`, `c002`, `c003`, `c004`
- selected_candidate_id: string
- best_score: number
- report_path: string
- extra: {
    host: "codex",
    model: "gpt-5.6-terra",
    rounds: 2,
    batch_sizes: [2, 2],
    agent_session_ids: array of four distinct strings,
    task_names: array of strings,
    round_2_parent_candidate_ids: array of strings
  }

If the run fails before producing a complete report, keep known identifiers,
leave candidates as the actual partial evidence, and put the precise failure
reason in `extra.error`.
