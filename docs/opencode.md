# OpenCode

> **Unsupported reference:** OpenCode is not maintained by the current
> parallel-loop runtime. Its assets and this setup snapshot may be stale, and
> its tests are excluded from the default gate. New Search specs must use Codex
> or Pi RPC.

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

## Historical Capabilities

- `/goal-plus` and the legacy `/goal-any-optimize` alias;
- legacy OpenCode-tested strategies;
- step-tiered candidate agents (15, 50, 100, 150 steps);
- same-session continuation through `Task(task_id=...)`;
- state-level redispatch through a fresh Task;

Goal Plus lifecycle checks are instruction-driven. The repository does not ship
OpenCode lifecycle hooks, so a missed gate is not enforced by OpenCode itself.
Search candidate workspaces, verifier history, selection, reports, and
promotion remain runtime-enforced.

## Worker Flow

The retained assets previously launched a foreground Task from
`search_start_agent_session`. The current public API no longer exposes the
OpenCode-only session-binding call, so this historical flow is not executable
as a supported path.

Do not use this snapshot as a workflow guide. The maintained flow is defined in
[Flow](flow-view.md), and current tools are indexed once in [API](api.md).

## Verification And Logs

```bash
pytest -m "st and st_opencode" -k k_module_smoke -v -s -rs
```

Runtime state is under `.gp/`. Existing OpenCode-native logs may still be
inspected as described in [Debugging](debugging-runtime.md).
