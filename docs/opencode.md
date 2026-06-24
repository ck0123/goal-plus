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
3. Runtime creates candidate workspaces under `.search/runs/<run_id>/workspace/`.
4. The host edits each candidate workspace.
5. Runtime verifies candidates and selects the score `1.0` candidate.
6. Runtime writes `report.md` and exports a promotion patch.

## Tool Prefix

OpenCode prefixes MCP tool names by server name. With `search-runtime`, tools appear as:

```text
search-runtime_search_freeze_spec
search-runtime_search_create
search-runtime_search_next_batch
search-runtime_search_submit_candidate
search-runtime_search_run_verifier
search-runtime_search_select
search-runtime_search_report
search-runtime_search_promote
search-runtime_search_abort
```

## Current Limit

This is a V0 host-guided flow. It does not yet spawn native OpenCode subagents or headless workers automatically. The main agent can act as the worker by editing candidate workspaces directly.

For the full walkthrough, see [toy-example.md](toy-example.md).
