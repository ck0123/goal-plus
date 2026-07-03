# Codex Reference

This project can run the Search MCP Runtime from Codex through the host adapter
introduced for `strategy.worker_host = "codex"`.

For the cross-host capability matrix and adapter contract, see
[agent-host-adapters.md](agent-host-adapters.md).

## Config

Project-local MCP configuration lives in:

```text
.codex/config.toml
.agents/skills/search/SKILL.md
.codex/agents/any_search_agent.toml
```

The MCP server is configured as:

```toml
[mcp_servers.search-runtime]
command = "agentic-any-search-mcp"
args = ["--root", ".search"]
cwd = "."
startup_timeout_sec = 10
tool_timeout_sec = 300
enabled = true
```

## Supported Strategies

Codex currently supports the portable builtin strategies only:

- `agent_guided`, `agent`, or `default`
- `random` or `random_mode`

OpenCode-specific or high-touch strategies such as `openevolve`, `evolve`,
`mcts`, Python strategy plugins, and external strategy drivers remain
OpenCode-only until they are adapted and tested.

## Worker Flow

The runtime returns a Codex-native foreground launch payload from
`search_start_agent_session`:

```json
{
  "tool": "spawn_agent",
  "task_name": "search_agent_001",
  "agent_type": "any_search_agent",
  "fork_turns": "none",
  "message": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

The main Codex agent should call `spawn_agent` with those fields, then record
the returned task name or nickname with `search_bind_agent_handle`.

Codex does not expose an equivalent same-worker continuation in this adapter.
When continuation is needed, start a new foreground worker for the same
candidate and use `search_get_agent_context` to recover the authoritative state.
