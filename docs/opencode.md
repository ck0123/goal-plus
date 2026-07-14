# OpenCode

OpenCode is the compatibility baseline for the existing advanced Search
strategies and the only host with project trace export.

## Setup

```bash
python -m pip install -e ".[dev]"
opencode mcp list
opencode
```

`opencode.json` launches:

```json
{
  "mcp": {
    "goal-plus": {
      "type": "local",
      "command": ["goal-plus", "--root", ".gp"],
      "cwd": ".",
      "timeout": 300000,
      "enabled": true
    }
  }
}
```

Headless entry:

```bash
opencode run --command goal-plus "<prompt>"
```

Project commands, skills, and agents live under `.opencode/`. The MCP server
configuration remains in `opencode.json`.

## Current Support

- `/goal-plus` and the legacy `/goal-any-optimize` alias;
- all existing OpenCode-tested builtin/Python strategies;
- step-tiered candidate agents (15, 50, 100, 150 steps);
- same-session continuation through `Task(task_id=...)`;
- state-level redispatch through a fresh Task;
- OpenCode trace export.

Goal Plus lifecycle checks are instruction-driven. The repository does not ship
OpenCode lifecycle hooks, so a missed gate is not enforced by OpenCode itself.
Search candidate workspaces, verifier history, selection, reports, and
promotion remain runtime-enforced.

## Worker Flow

`search_start_agent_session` returns `subagent_type`, `description`, and
`prompt`. The main agent launches a foreground Task, binds
`metadata.sessionId` with `search_bind_opencode_session`, and final-verifies
after return.

For continued native context:

```text
search_continue_agent_session -> Task(task_id=...)
```

For a fresh worker in the same candidate workspace:

```text
search_redispatch_candidate -> Task(...)
```

Task step caps are host limits, not Search budgets. The main agent follows the
shared [Flow](flow-view.md); current tools are indexed once in [API](api.md).

## Verification And Logs

```bash
pytest -m "st and st_opencode" -k k_module_smoke -v -s -rs
```

Runtime state is under `.gp/`. OpenCode session/tool evidence and trace export
are documented in [Debugging](debugging-runtime.md). Strategy-specific details
are in [OpenEvolve](strategy-openevolve.md) and
[AdaptEvolve](strategy-adaptevolve.md).
