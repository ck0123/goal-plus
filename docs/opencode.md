# OpenCode Reference

This project ships a local OpenCode setup for running the Search MCP Runtime:

```text
.opencode/opencode.json
.opencode/skills/search/SKILL.md
.opencode/agents/search-orchestrator.md
examples/k_module_search_spec.json
```

## Start

From the project root:

```bash
OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true opencode
```

OpenCode should start the local MCP server named `search-runtime` using:

```bash
PYTHONPATH=src python -m agentic_any_search_mcp.server --root .search
```

The server uses stdio transport.

The background-subagent flag must be set on the OpenCode process. Setting it only in `.opencode/opencode.json` under the MCP server environment is not enough, because that environment belongs to the Python MCP subprocess, not the OpenCode `Task` tool.

For headless runs, use the same environment:

```bash
OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true opencode run --command search "<prompt>"
```

Current OpenCode `Task` exposes `background: true` behind this flag, but it does not expose a Task-level `timeout` parameter. Search timeouts are enforced by MCP session deadlines and the supervisor wait loop.

## Verify MCP Connectivity

```bash
opencode mcp list
```

Expected entry:

```text
search-runtime connected
python -m agentic_any_search_mcp.server --root .search
```

You can also run a safe negative probe:

```bash
opencode run "Use the MCP tool search-runtime_search_status with run_id='missing-opencode-smoke'. Do not edit files. Report whether the tool was available."
```

The expected result is that the tool is callable and reports that the run does not exist.

## Run The Toy Search

In OpenCode:

```text
/search run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py.
```

Headless:

```bash
OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true opencode run --command search "Run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Keep all edits inside candidate workspaces."
```

Expected behavior:

1. The `search` skill loads `examples/k_module_search_spec.json` or drafts an equivalent SearchSpec.
2. Runtime freezes `tests/fixtures/k_module_problem/evaluator.py`.
3. Runtime plans the next strategy step and creates candidate workspaces under `.search/runs/<run_id>/workspace/`.
4. The host edits each candidate workspace.
5. Runtime verifies candidates and selects the score `1.0` candidate.
6. Runtime writes `report.md` with strategy/candidate details and exports a promotion patch.

## Tool Prefix

OpenCode prefixes MCP tool names by server name. With `search-runtime`, tools appear as:

```text
search-runtime_search_freeze_spec
search-runtime_search_create
search-runtime_search_status
search-runtime_search_list_history
search-runtime_search_plan_next
search-runtime_search_start_batch
search-runtime_search_next_batch
search-runtime_search_start_agent_session
search-runtime_search_get_agent_context
search-runtime_search_update_agent_status
search-runtime_search_list_agent_status
search-runtime_search_finish_agent_session
search-runtime_search_request_agent_finalize
search-runtime_search_abort_agent_session
search-runtime_search_abort_all_agent_sessions
search-runtime_search_publish_observation
search-runtime_search_list_observations
search-runtime_search_wait_agent_events
search-runtime_search_submit_candidate
search-runtime_search_run_verifier
search-runtime_search_select
search-runtime_search_report
search-runtime_search_promote
search-runtime_search_abort
```

## Agent Session Pool

The autonomous-search control plane represents each long-running subagent as an agent session:

1. Main agent creates candidate workspaces with `search_start_batch`.
2. Main agent starts sessions with `search_start_agent_session(run_id, candidate_id, directive, budget)`.
3. Runtime enforces `budget.max_parallel` as the active session pool size; attempts above the pool fail instead of relying on prompt discipline.
4. Main agent launches `AnySearchAgent` with the returned `agent_session_id` through an OpenCode Task call with `background: true`, which returns control immediately. Do not use foreground long-running Task calls. If the host cannot launch background/managed tasks, use direct candidate work or stop instead of pretending the run is supervised.
5. Subagents call `search_get_agent_context(agent_session_id)`, then read/edit their workspace. They may call `search_update_agent_status` sparingly after meaningful progress or when blocked, but should not do status heartbeats before the first file read. Shared findings can be published with `search_publish_observation`.
6. The supervisor loop calls `search_wait_agent_events(run_id, timeout_seconds=300, since_event_id=<last_event_id>)` and feeds the returned `last_event_id` into the next wait call.
   It returns when a session completes/fails/blocks/times out, when the run deadline is reached, or when the wait timeout expires with a status snapshot.
7. Completed sessions are finalized with `search_finish_agent_session`; stuck sessions can be nudged with `search_request_agent_finalize` or stopped with `search_abort_agent_session`.
8. When the run budget is exhausted, call `search_abort_all_agent_sessions` and summarize/verify the best submitted candidates.

The runtime owns durable pool, deadline, event, and observation state. `worker_timeout_seconds` is a runtime/session deadline, not an OpenCode Task timeout. Hard process/session cancellation still requires the host adapter to wire `search_abort_agent_session` to OpenCode's native abort for the child session; the MCP state transition is the control-plane source of truth.

For the full walkthrough, see [toy-example.md](toy-example.md).
