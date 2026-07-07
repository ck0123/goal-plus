---
name: search
description: Run Search Mode from Pi with Pi RPC workers.
---

# Pi Search Mode

Use this flow exactly for `worker_host="pi-rpc"`:

1. `search_plan_next`
2. `search_start_batch`
3. For each candidate, call `search_start_agent_session`.
4. Pass the returned `launch` object to `pi_rpc_run_worker`.
5. Immediately call `search_bind_agent_handle(agent_session_id, handle)` with the handle returned by `pi_rpc_run_worker`.
6. After workers return, run final search_run_verifier without `agent_session_id` for each candidate that should be selected.
7. Call `search_select`, `search_report`, and `search_promote` when promotion is requested.

Worker launch is foreground and synchronous. `worker_budget.max_runtime_seconds` is required and maps to the Pi RPC process watchdog. `worker_budget.max_turns` is only a prompt hint.

Continuation uses `session_jsonl_restart`: `search_continue_agent_session` returns another `pi_rpc_run_worker` launch using the same Pi `--session-id`; it is not a live stdin continuation. If a worker times out or exits before producing useful verifier evidence, prefer `search_redispatch_candidate` to create a new `agent_session_id` for the same candidate workspace.

History is runtime-owned, not a local plan file. Workers must call `search_get_agent_context` first and use `context.history` plus `context.iterations` as the resume source.

For optimization tasks, require workers to create a complete candidate artifact and run an early `search_run_verifier` before any long local optimization loop. For fix/target tasks, require the allowed-file edit before the verifier call; do not count verification of the unmodified starting point as worker evidence. Search progress must be visible as verifier-recorded runtime iterations, not hidden in the worker transcript or scratch scripts.
