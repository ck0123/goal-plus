# ST Final Report Schema

Every ST prompt MUST end with the following instruction so the host main agent
emits a machine-parseable report at the end of stdout.

Append this block verbatim to each prompt file (after the scenario-specific body):

```
## ST Output Contract

When the search is complete, output a fenced JSON block tagged `st_report` as the
LAST thing in your final message. No prose after the block. The JSON MUST conform
to this schema:

- scenario: string         # one of circle_packing_continue | circle_packing_two_batch | circle_packing_random | k_module_smoke | k_module_then_circle_packing | signal_processing_multi | swe_bench_20212 | codex_redispatch | codex_circle_packing_cycle | codex_rolling_followup | claude_k_module_smoke
- run_id: string           # search runtime run_id
- candidates: array of { candidate_id: string, score: number|null, iterations: integer, status: string }
- selected_candidate_id: string | null
- best_score: number | null
- report_path: string      # path to runtime-generated report.md inside the workspace
- extra: object            # scenario-specific fields (see prompt body)

If the run failed before producing a report, set scenario/run_id as known, leave
candidates an empty array, selected_candidate_id null, best_score null, and put
the failure reason in extra.error.
```

Scenario-specific `extra` fields:

| scenario | extra fields |
|---|---|
| circle_packing_continue | `agent_session_id`, `opencode_session_id`, `verifier_scores: [number, number]`, `score_delta: number` |
| circle_packing_two_batch | (none beyond defaults) |
| circle_packing_random | `parent_candidate_id: string` (batch-2 parent from strategy_trace) |
| k_module_smoke | (none) |
| k_module_then_circle_packing | `run1_run_id: string`, `run1_candidates: integer`, `run1_best_score: number\|null`, `run2_run_id: string`, `run2_candidates: integer`, `run2_best_score: number\|null`, `run_ids_distinct: boolean` |
| signal_processing_multi | `batches: integer` |
| swe_bench_20212 | `fail_to_pass: array<string>`, `pass_to_pass: array<string>` |
| codex_redispatch | `host: "codex"`, `model: string`, `candidate_id: string`, `first_agent_session_id: string`, `redispatch_agent_session_id: string`, `same_candidate: boolean`, `redispatch_budget_control_mode: "parent_watchdog"`, `task_names: array<string>`, `verifier_scores: array<number\|null>` |
| codex_circle_packing_cycle | `host: "codex"`, `model: "gpt-5.6-terra"`, `rounds: 2`, `batch_sizes: [2, 2]`, `agent_session_ids: array of four distinct strings`, `task_names: array<string>`, `round_2_parent_candidate_ids: array<string>` |
| codex_rolling_followup | `host: "codex"`, `model: "gpt-5.6-terra"`, `wait_mode: "wait_any"`, two initial session ids/task names, first/continued candidate ids, unchanged continued session id, `continue_tool: "followup_task"`, `same_worker_continuation: true` |
| claude_k_module_smoke | `host: "claude-code"` |
