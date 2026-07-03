# OpenCode Reference

This project ships a local OpenCode setup for running the Search MCP Runtime:

```text
opencode.json
.opencode/skills/search/SKILL.md
.opencode/agents/search-orchestrator.md
examples/k_module_search_spec.json
```

OpenCode remains the compatibility baseline. Its launch payload,
`search_bind_opencode_session`, and `Task(task_id=...)` continuation are
intentionally preserved while Codex and Claude Code use separate host adapters.

For the cross-host capability matrix and adapter contract, see
[agent-host-adapters.md](agent-host-adapters.md).

## Install The MCP Server

Install this Python package so the `agentic-any-search-mcp` command is available
on `PATH`:

From Git:

```bash
python -m pip install --user "git+https://gitcode.com/yiyanzhi_akane1/agentic-any-search-mcp.git"
agentic-any-search-mcp --help
```

From an existing checkout:

```bash
cd agentic-any-search-mcp
python -m pip install -e .
agentic-any-search-mcp --help
```

The OpenCode MCP config should call the console script, not import from the
source tree through `PYTHONPATH`. This package is not published to PyPI yet, so
do not document direct PyPI install commands until that release exists.

## Config Scope

OpenCode config files are merged by scope. Use the smallest scope that matches
the use case:

- Project or directory-level: `opencode.json` in the project root. This is the
  checked-in setup used by this repository.
- User/global: `~/.config/opencode/opencode.json`. Use this when one person
  wants the MCP server available across many projects.
- Custom one-off config: set `OPENCODE_CONFIG=/path/to/opencode.json`.
- Managed organization config: use OpenCode's platform-specific managed config
  locations when admins need enforced defaults.

The `.opencode/` directory remains useful for project-local agents, skills, and
commands. It is not the preferred place for the main MCP server config.

All scopes should configure the same installed command:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "search-runtime": {
      "type": "local",
      "command": [
        "agentic-any-search-mcp",
        "--root",
        ".search"
      ],
      "cwd": ".",
      "timeout": 300000,
      "enabled": true
    }
  }
}
```

## Updating

For a Git install through user-level pip:

```bash
python -m pip install --user -U "git+https://gitcode.com/yiyanzhi_akane1/agentic-any-search-mcp.git"
```

For editable development installs:

```bash
cd agentic-any-search-mcp
git pull
python -m pip install -e ".[dev]"
python -m pytest -q
opencode mcp list
```

## Start

From the project root:

```bash
opencode
```

OpenCode should start the local MCP server named `search-runtime` using:

```bash
agentic-any-search-mcp --root .search
```

The server uses stdio transport.

For headless runs:

```bash
opencode run --command search "<prompt>"
```

Current OpenCode `Task` does not expose a Task-level `timeout` parameter. Subagents run until their OpenCode step cap hits or the user interrupts the run; there are no per-session or run-level time deadlines, and there is no MCP abort tool.

## Verify MCP Connectivity

```bash
opencode mcp list
```

Expected entry:

```text
search-runtime connected
agentic-any-search-mcp --root .search
```

You can also run a safe negative probe:

```bash
opencode run "Use the MCP tool search-runtime_search_status with run_id='missing-opencode-smoke'. Do not edit files. Report whether the tool was available."
```

The expected result is that the tool is callable and reports that the run does not exist.

## Run The Toy Search

In OpenCode:

```text
Load examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Then run the k_module smoke test end-to-end (freeze → create → plan → batch → sessions → verify → select → report).
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

OpenCode prefixes MCP tool names by server name. With `search-runtime`, the only tools are:

```text
search-runtime_search_freeze_spec
search-runtime_search_create
search-runtime_search_status
search-runtime_search_list_history
search-runtime_search_plan_next
search-runtime_search_start_batch
search-runtime_search_start_agent_session
search-runtime_search_bind_agent_handle
search-runtime_search_bind_opencode_session
search-runtime_search_continue_agent_session
search-runtime_search_get_agent_context
search-runtime_search_run_verifier
search-runtime_search_list_iterations
search-runtime_search_select
search-runtime_search_report
search-runtime_search_promote
```

There are no wait, abort, finish, submit, observation, status, or host-sync tools.

## Agent Session Pool

The autonomous-search control plane represents each long-running subagent as an OpenCode Task launched from an MCP context handle:

1. Main agent creates candidate workspaces with `search_start_batch`.
2. Main agent calls `search_start_agent_session(run_id, candidate_id, directive)` to obtain a context handle plus a `launch` payload (`subagent_type`, `description`, `prompt`).
3. Main agent launches the OpenCode Task using the launch payload verbatim as a foreground Task call.
4. When Task metadata is available, main agent calls `search_bind_opencode_session(agent_session_id, opencode_session_id=<Task metadata.sessionId>)`.
5. Subagents call `search_get_agent_context(agent_session_id)`, then read/edit their workspace and self-score with `search_run_verifier(..., agent_session_id=...)`. The only required MCP calls are those two.
6. Main agent waits for OpenCode Task to return. There is no MCP wait loop.
7. After a Task returns, the main agent runs `search_run_verifier(run_id, candidate_id, "process")` to confirm the current best workspace state.
8. To continue the same candidate/node, main agent calls `search_continue_agent_session(agent_session_id, directive?)` and launches `Task(task_id=launch.task_id, ...)`. This reuses the same OpenCode session and candidate workspace; it is not fork/branch creation.
9. When the run budget is exhausted, the main agent stops launching new Tasks and reports the best candidates. Stopping a running subagent is an OpenCode/user interruption concern; there is no MCP abort.

The runtime owns specs, plans, workspaces, verifier scoring, history, reports, and promotion. OpenCode owns the actual subagent lifecycle (start, step cap, stop/interrupt, Task return). The runtime does not maintain lifecycle status, host-sync state, or process cancellation.

For the full walkthrough, see [toy-example.md](toy-example.md).

For the per-step information flow — which agent sees which fields at each stage, and which OpenCode platform constraints gate the flow — see [flow-view.md](flow-view.md). That doc is the reference for designing strategy changes (evolve, mcts, hybrid) without building on APIs the platform does not actually expose.
