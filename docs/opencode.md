# OpenCode Reference

This project ships a local OpenCode setup for running `/goal-plus` over the
Search MCP Runtime:

```text
opencode.json
.opencode/command/goal-plus.md
.opencode/command/goal-any-optimize.md
.opencode/skills/goal-plus/SKILL.md
.opencode/skills/search/SKILL.md
.opencode/agents/goal-plus-orchestrator.md
.opencode/agents/search-orchestrator.md
examples/k_module_search_spec.json
```

OpenCode remains the compatibility baseline. Its launch payload,
`search_bind_opencode_session`, and `Task(task_id=...)` continuation are
intentionally preserved while Codex and Claude Code use separate host adapters.

This means OpenCode is the baseline for Search Mode worker orchestration, not
for enforced Goal Plus lifecycle control. The checked-in OpenCode assets do not
include a `Stop` or `PreToolUse` hook that calls `goal_plus_gate`; the
`goal-plus-orchestrator` is instructed to call the gate manually. If it skips
that instruction, OpenCode will not automatically block the final answer or an
early Search Mode tool call.

For the cross-host capability matrix and adapter contract, see
[agent-host-adapters.md](agent-host-adapters.md).

For runtime and host log inspection, see
[debugging-runtime.md](debugging-runtime.md).

## Install The MCP Server

Install this Python package so the `goal-plus` command is available
on `PATH`:

From Git:

```bash
python -m pip install --user "git+https://github.com/ck0123/goal-plus.git"
goal-plus --help
```

From an existing checkout:

```bash
cd goal-plus
python -m pip install -e .
goal-plus --help
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
    "goal-plus": {
      "type": "local",
      "command": [
        "goal-plus",
        "--root",
        ".gp"
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
python -m pip install --user -U "git+https://github.com/ck0123/goal-plus.git"
```

For editable development installs:

```bash
cd goal-plus
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

OpenCode should start the local MCP server named `goal-plus` using:

```bash
goal-plus --root .gp
```

The server uses stdio transport.

For headless runs:

```bash
opencode run --command goal-plus "<prompt>"
```

Current OpenCode `Task` does not expose a Task-level `timeout` parameter. Subagents run until their OpenCode step cap hits or the user interrupts the run; there are no per-session or run-level time deadlines, and there is no MCP abort tool.

## Goal Plus Enforcement

OpenCode support is currently instruction-driven:

1. `/goal-plus` loads the `goal-plus-orchestrator`.
2. The orchestrator calls `goal_plus_create`, triage/spec-draft tools, and
   manual `goal_plus_gate` checks at the points documented in the skill.
3. After Search Mode starts, OpenCode Task workers run through the internal
   `search` skill flow.

There is no checked-in OpenCode hook adapter that automatically invokes:

- `goal_plus_gate(event="pre_tool_use", ...)` before `search_*`
- `goal_plus_gate(event="stop", ...)` before the main agent stops

Therefore `/goal-plus` on OpenCode should be described as best-effort lifecycle
control plus real Search Mode worker orchestration. A future OpenCode hook or
external runner would be needed before calling it hook-enforced Goal Plus.

## Verify MCP Connectivity

```bash
opencode mcp list
```

Expected entry:

```text
goal-plus connected
goal-plus --root .gp
```

You can also run a safe negative probe:

```bash
opencode run "Use the MCP tool goal-plus_search_status with run_id='missing-opencode-smoke'. Do not edit files. Report whether the tool was available."
```

The expected result is that the tool is callable and reports that the run does not exist.

## Run The Toy Search

In OpenCode:

```text
Use /goal-plus. Load examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Show and confirm the frozen verifier, metric, edit surface, and promotion rule before Search Mode. Then run the k_module smoke test end-to-end.
```

Headless:

```bash
opencode run --command goal-plus "Run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. This prompt explicitly confirms the frozen verifier, metric, edit surface, and promotion rule. Keep all edits inside candidate workspaces."
```

Expected behavior:

1. The `goal-plus` skill creates a goal-plus record and records triage.
2. For this initial search-ready task, the agent saves a spec draft and records frozen-verifier confirmation.
3. The internal `search` skill freezes `tests/fixtures/k_module_problem/evaluator.py`.
4. Runtime plans the next strategy step and creates candidate workspaces under `.gp/runs/<run_id>/workspace/`.
5. The host edits each candidate workspace.
6. Runtime verifies candidates and selects the score `1.0` candidate.
7. Runtime writes `report.md` with strategy/candidate details and exports a promotion patch.

## Tool Prefix

OpenCode prefixes MCP tool names by server name. With `goal-plus`, the
goal-plus tools are:

```text
goal-plus_goal_plus_create
goal-plus_goal_plus_status
goal-plus_goal_plus_record_triage
goal-plus_goal_plus_save_spec_draft
goal-plus_goal_plus_confirm_frozen_verifier
goal-plus_goal_plus_link_search_run
goal-plus_goal_plus_record_search_result
goal-plus_goal_plus_set_status
goal-plus_goal_plus_gate
```

The internal Search Mode engine tools are:

```text
goal-plus_search_freeze_spec
goal-plus_search_create
goal-plus_search_status
goal-plus_search_list_history
goal-plus_search_plan_next
goal-plus_search_start_batch
goal-plus_search_start_agent_session
goal-plus_search_redispatch_candidate
goal-plus_search_bind_agent_handle
goal-plus_search_bind_opencode_session
goal-plus_search_continue_agent_session
goal-plus_search_get_agent_context
goal-plus_search_run_verifier
goal-plus_search_list_iterations
goal-plus_search_select
goal-plus_search_report
goal-plus_search_promote
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
8. To continue the same candidate/node in the same OpenCode context, main agent calls `search_continue_agent_session(agent_session_id, directive?)` and launches `Task(task_id=launch.task_id, ...)`. This reuses the same OpenCode session and candidate workspace; it is not fork/branch creation.
9. To recover from a step-cap hit or upgrade the worker tier for the same candidate workspace, main agent calls `search_redispatch_candidate(run_id, candidate_id, directive?, worker_agent_type?)` and launches a fresh Task from the returned payload. This creates a new `agent_session_id`; it does not create a new candidate.
10. When the run budget is exhausted, the main agent stops launching new Tasks and reports the best candidates. Stopping a running subagent is an OpenCode/user interruption concern; there is no MCP abort.

The runtime owns specs, plans, workspaces, verifier scoring, history, reports, and promotion. OpenCode owns the actual subagent lifecycle (start, step cap, stop/interrupt, Task return). The runtime does not maintain lifecycle status, host-sync state, or process cancellation.

For the full walkthrough, see [toy-example.md](toy-example.md).

For the per-step information flow — which agent sees which fields at each stage, and which OpenCode platform constraints gate the flow — see [flow-view.md](flow-view.md). That doc is the reference for designing strategy changes (evolve, mcts, hybrid) without building on APIs the platform does not actually expose.
