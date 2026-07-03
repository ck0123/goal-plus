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

If `search_continue_agent_session` returns a `SendMessage` payload, send the
message to that foreground agent. If no handle is bound, start a new foreground
Agent for the same candidate and use `search_get_agent_context` to recover the
authoritative state.
