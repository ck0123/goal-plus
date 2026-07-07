# Codex Reference

This project can run `/goal-plus` from Codex. Search Mode uses the Search MCP
Runtime through the host adapter introduced for `strategy.worker_host =
"codex"`.

For the cross-host capability matrix and adapter contract, see
[agent-host-adapters.md](agent-host-adapters.md).

## Config

Project-local MCP configuration lives in:

```text
.codex/config.toml
.codex/hooks.json
.agents/skills/goal-plus/SKILL.md
.agents/skills/search/SKILL.md
.codex/agents/any_search_agent.toml
scripts/hooks/goal_plus_stop.py
```

Use `goal-plus` as the user-facing skill. The `search` skill is the internal
Search Mode engine after Goal Plus has frozen and, when needed, confirmed a
verifier-backed spec.

This repository ships project-local Goal Plus host hooks:
`.codex/hooks.json` runs `agentic-any-search-mcp --goal-plus-host-hook` for
`PostToolUse` and `Stop`. Codex project hooks must be reviewed and trusted
through `/hooks` before they run.

`PostToolUse(goal_plus_create)` binds the created Goal Plus record to the
current top-level Codex `session_id`. Subagent tool events are ignored for
ownership binding. The Stop hook gates only an explicitly selected
`GOAL_PLUS_ID` or an active Goal Plus record whose bound session matches the
current Codex session. It does not wire PreToolUse or SubagentStop hooks. The
skill still calls `goal_plus_gate` manually before Search Mode tools and
before the final response.

Set `GOAL_PLUS_ID=gp_...` to force the hook to gate a specific active goal when
multiple Goal Plus records are active. Set `GOAL_PLUS_STOP_HOOK_DISABLED=1` or
`GOAL_PLUS_HOST_HOOK_DISABLED=1` to temporarily bypass the hooks.

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

After `/goal-plus` enters Search Mode, the runtime returns a Codex-native
foreground launch payload from
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

## Worker Budget

Codex `spawn_agent` does not accept a spawn-time timeout or step limit. This
adapter therefore enforces elapsed worker time through a parent watchdog.

Example spec:

```json
{
  "strategy": {
    "name": "random",
    "worker_host": "codex",
    "worker_budget": {
      "max_runtime_seconds": 600,
      "max_turns": 8,
      "on_exceed": "interrupt"
    }
  }
}
```

The returned launch payload includes:

```json
{
  "budget_control": {
    "mode": "parent_watchdog",
    "max_runtime_seconds": 600,
    "wait_timeout_ms": 600000,
    "on_exceed": "interrupt",
    "interrupt_target": "search_agent_001",
    "max_turns_hint": 8
  }
}
```

The parent Codex agent must wait with `wait_agent(timeout_ms=...)`. If the wait
times out, it interrupts the child with `interrupt_agent` when available, or
with `send_input(..., interrupt=true)` on Codex surfaces that expose interruption
through message sending.

`max_turns` is only a hint for Codex workers. The enforceable control is
`max_runtime_seconds`.

If a worker is interrupted before it records any verifier iteration or usable
score, treat the budget as too small for that task. For later planned work,
call `search_redispatch_candidate` for the same candidate with a larger
`worker_budget.max_runtime_seconds`; Codex does not expose a hard per-subagent
step tier.

Codex does not expose an equivalent same-worker continuation in this adapter.
When continuation is needed, call `search_redispatch_candidate` to start a new
foreground worker for the same candidate and use `search_get_agent_context` to
recover the authoritative state. The worker should use `context.history` and
`context.iterations` from the MCP runtime; there is no `plan.md` history file
for Search Mode.

## Debugging Logs

For the cross-host debugging workflow, see
[debugging-runtime.md](debugging-runtime.md).

Codex has two useful log surfaces for this adapter:

- `codex exec --json ... > .search/host-logs/codex-<timestamp>.jsonl` captures
  the full non-interactive event stream for a reproducible run.
- `${CODEX_HOME:-~/.codex}/sessions/YYYY/MM/DD/rollout-*.jsonl` stores persisted
  local rollout transcripts unless the run used `codex exec --ephemeral`.

For interactive CLI diagnostics, start Codex with a plaintext log directory:

```bash
RUST_LOG=debug codex -c log_dir=./.codex-log
tail -F ./.codex-log/codex-tui.log
```

When debugging worker budgets, search Codex logs for `agent_session_id`,
`candidate_id`, `spawn_agent`, `wait_agent`, `send_input`, `interrupt`,
`budget_control`, `turn.completed`, and `turn.failed`.

`codex exec -o <file>` writes only the final message. Use `--json` or the
persisted rollout transcript when you need tool calls, spawned-agent events, or
MCP calls.
