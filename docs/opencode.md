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
opencode
```

OpenCode should start the local MCP server named `search-runtime` using:

```bash
PYTHONPATH=src python -m agentic_any_search_mcp.server --root .search
```

The server uses stdio transport.

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
opencode run --command search "Run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Keep all edits inside candidate workspaces."
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
search-runtime_search_prepare_worker
search-runtime_search_get_worker_context
search-runtime_search_submit_candidate
search-runtime_search_run_verifier
search-runtime_search_select
search-runtime_search_report
search-runtime_search_promote
search-runtime_search_abort
```

## Current Limit

This is a V0 host-guided flow. It does not yet spawn native OpenCode subagents or headless workers automatically. The main agent can act as the worker by editing candidate workspaces directly.

If `strategy.worker_mode` is `sub-agent-search-dispatch`, use the worker dispatch protocol:

1. Main agent calls `search-runtime_search_prepare_worker(run_id, candidate_id, main_directive)`.
   `main_directive` may be a plain string or a structured object.
2. Main agent launches the worker with `worker_policy.subagent_type` when present. Bundled dispatch examples use `subagent_type="AnySearchAgent"`.
3. Main agent passes the returned `dispatch_id` or `worker_brief` to the subagent.
4. Subagent first calls `search-runtime_search_get_worker_context(dispatch_id)`.
5. Subagent works only in the returned `workspace`, uses the returned `scratch_dir`, and returns/submits an artifact with `dispatch_id` and `context_hash`.
6. Main agent treats `worker_policy.timeout_seconds` and worker context `deadline_at` as the collection deadline. Default timeout is 600 seconds; pass `timeout_seconds` to `search_prepare_worker` to override one dispatch.
7. By default `worker_policy.local_verifier_max_runs` is 0: subagents must not run the process verifier command, evaluator APIs, equivalent local scorers, score-driven sweeps, or custom scratch scripts that execute the candidate to estimate quality. They may run non-scoring static checks such as `py_compile`. Runtime-owned `search_run_verifier` after submission is the actual verification path.

Dispatch audit files are written under `.search/runs/<run_id>/dispatches/`.

The main agent must call `search-runtime_search_run_verifier` for every submitted candidate before selection. Worker-reported scores are not authoritative. Worker directives should not contain numeric score targets or baseline scores; those encourage local scoring loops. The timeout is a host-side collection rule; V0 does not rely on MCP to terminate the OpenCode subagent process. Final candidate code should be bounded and fast; workers should not put long parameter sweeps or open-ended optimization loops in the final allowed file.

If `strategy.worker_mode` is `main-agent-search-direct`, the host agent can edit the candidate workspace directly.

For the full walkthrough, see [toy-example.md](toy-example.md).
