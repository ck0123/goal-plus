# Claude Code Reference

This project can run the Search MCP Runtime from Claude Code through the host
adapter introduced for `strategy.worker_host = "claude-code"`.

For the cross-host capability matrix and adapter contract, see
[agent-host-adapters.md](agent-host-adapters.md).

## Version

The implementation does not require upgrading Claude Code. The local version
verified during design was:

```text
2.1.142 (Claude Code)
```

Newer Claude Code versions may expose richer subagent management, but this
adapter only relies on foreground Agent launches and optional `SendMessage`
continuation when Claude Code exposes a reusable agent handle.

## Config

Project-local assets:

```text
.mcp.json
.claude/skills/search/SKILL.md
.claude/agents/any-search-agent.md
.claude/agents/any-search-agent-flash.md
.claude/agents/any-search-agent-deep.md
```

The MCP server is configured as:

```json
{
  "mcpServers": {
    "search-runtime": {
      "command": "agentic-any-search-mcp",
      "args": ["--root", ".search"]
    }
  }
}
```

## Supported Strategies

Claude Code currently supports the portable builtin strategies only:

- `agent_guided`, `agent`, or `default`
- `random` or `random_mode`

OpenCode-specific or high-touch strategies such as `openevolve`, `evolve`,
`mcts`, Python strategy plugins, and external strategy drivers remain
OpenCode-only until they are adapted and tested.

## Worker Flow

The runtime returns a Claude Code foreground launch payload from
`search_start_agent_session`:

```json
{
  "tool": "Agent",
  "agent_type": "any-search-agent",
  "background": false,
  "message": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

The main Claude Code agent should launch a foreground Agent using those fields.
If the Agent result includes a reusable agent id or name, record it with
`search_bind_agent_handle`.

## Worker Budget

Claude Code enforces worker runtime through `maxTurns` in the selected agent
definition. The runtime returns `budget_control` metadata so the main agent can
verify that it is launching the intended bounded agent type.

Example spec:

```json
{
  "strategy": {
    "name": "random",
    "worker_host": "claude-code",
    "worker_agent_type": "any-search-agent-deep",
    "worker_budget": {
      "max_turns": 16,
      "on_exceed": "interrupt"
    }
  }
}
```

Available bounded workers:

| Agent type | Turn budget |
|---|---:|
| `any-search-agent-flash` | 4 |
| `any-search-agent` | 8 |
| `any-search-agent-deep` | 16 |

If `worker_agent_type` is omitted, the adapter maps `max_turns` 4, 8, and 16
to those three known agent types. If a known `worker_agent_type` is provided,
its configured turn budget must match `worker_budget.max_turns`. Custom Claude
agent types are allowed, but then their own definition must carry the matching
`maxTurns` value.

Claude Code does not use the Codex parent-watchdog flow for normal workers.
The enforcement comes from the chosen foreground Agent definition.

This is a subagent/Agent-definition limit. A top-level `claude -p --agent ...`
session is not treated as proof that `maxTurns` applies to a dispatched search
worker; verify budget behavior through an actual `Agent`/`Task` subagent launch.

If `search_continue_agent_session` returns a `SendMessage` payload, send the
message to that foreground agent. If no handle is bound, start a new foreground
Agent for the same candidate and use `search_get_agent_context` to recover the
authoritative state.

## Debugging Logs

For the cross-host debugging workflow, see
[debugging-runtime.md](debugging-runtime.md).

For reproducible adapter runs, capture both stream output and debug logs:

```bash
mkdir -p .search/host-logs
claude -p --verbose --output-format stream-json \
  --debug-file .search/host-logs/claude-debug.log \
  "<search prompt>" \
  > .search/host-logs/claude.jsonl
```

Add `--include-hook-events` for hook debugging or
`--include-partial-messages` for partial stream events. `--debug-file`
implicitly enables debug mode.

Claude Code persists parent transcripts under
`~/.claude/projects/<encoded-project>/<session>.jsonl`; subagent transcripts
live under the matching `<session>/subagents/` directory, and large tool outputs
may spill into `<session>/tool-results/`. The safest project-state locator is:

```bash
claude project purge "$PWD" --dry-run
```

Do not remove `--dry-run` unless deleting the project's local Claude Code state
is intentional.

When debugging worker budgets, search stream/debug logs for `agent_session_id`,
`candidate_id`, `task_started`, `task_progress`, `task_notification`,
`subagent_type`, `Reached max turns limit`, and `Agent:`.
