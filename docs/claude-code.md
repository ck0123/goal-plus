# Claude Code

Claude Code is a compatibility host for the portable Search strategies. It
uses foreground `Agent` workers and the shared MCP runtime.

## Setup

```bash
python -m pip install -e ".[dev]"
claude
```

Project assets are `.mcp.json`, `.claude/settings.json`, `.claude/skills/`, and
`.claude/agents/`. The MCP entry is:

```json
{
  "mcpServers": {
    "goal-plus": {
      "command": "goal-plus",
      "args": ["--root", ".gp"]
    }
  }
}
```

This repository ships Claude Code Goal Plus host hooks through
`goal-plus --goal-plus-host-hook`. `PostToolUse(goal_plus_create)` binds the
top-level session and `Stop` is a session-scoped backstop. The project does not wire PreToolUse or SubagentStop hooks;
those checkpoints remain explicit skill calls. The Stop hook catches missed
completion gates but does not supervise workers. Every still-active top-level
record receives the full raw goal and elapsed-time context until the main agent
records a terminal status.

## Current Support

| Capability | Status |
|---|---|
| Search runtime and isolated candidates | supported |
| Foreground Agent launch | supported |
| Worker budget | `worker_budget.max_turns` mapped to agents with 4, 8, or 16 `maxTurns` |
| State-level redispatch | supported |
| Same-worker messaging | conditional; use redispatch as the portable fallback |
| Goal creation binding | PostToolUse hook |
| Top-level completion gate | Stop hook |
| Pre-tool and subagent-stop enforcement | instruction-driven |
| Trace export and normalized cost metadata | not implemented |

The local version used for the original adapter evidence was Claude Code
2.1.142. This is a snapshot, not a current-version claim.

## Worker Flow

`search_start_agent_session` returns a foreground launch payload:

```json
{
  "tool": "Agent",
  "agent_type": "search-candidate-agent",
  "description": "c001 try alternate parser",
  "background": false,
  "message": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

Launch it, bind any returned native id with `search_bind_agent_handle`, and
final-verify after Agent returns. Known budgets map to
`search-candidate-agent-flash`, `search-candidate-agent`, or
`search-candidate-agent-deep`. If the worker stops too early, call
`search_redispatch_candidate` with a larger one-dispatch budget.

Claude Code supports `agent_guided`/`agent`/`default` and
`random`/`random_mode`. The main agent follows the canonical
[Flow](flow-view.md); no host-specific API copy is maintained here.

## Verification And Logs

Fast tests:

```bash
pytest tests/test_claude_assets.py -q
```

Capture headless evidence with:

```bash
claude -p "..." --output-format stream-json --verbose \
  --debug-file .gp/host-logs/claude-debug.log
```

Useful paths are `~/.claude/projects`, per-session `subagents/`, and the chosen
`--debug-file`. Use `claude project purge` only when intentionally clearing
host history. Cross-host diagnosis is in [Debugging](debugging-runtime.md), and
the capability comparison is in
[Agent Host Adapters](agent-host-adapters.md).
