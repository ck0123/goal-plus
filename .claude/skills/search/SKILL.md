---
name: search
description: Internal Search Mode engine for /goal-plus with foreground Claude Code agents and the search-runtime MCP server.
---

# Search Mode Runtime for Claude Code

Use this skill after `/goal-plus` has upgraded a goal to Search Mode, or for
explicit low-level debugging of an already measurable SearchSpec. The normal
user-facing entrypoint is `/goal-plus`.

Use the logical `search_*` tools exposed by the `search-runtime` MCP server.
Claude Code may display MCP tools with a server prefix; match by the final
logical tool name.

## Main Workflow

1. Call `search_freeze_spec` for the Goal Plus spec draft, or `search_create`
   when a frozen spec already exists.
2. Call `search_plan_next`.
3. Call `search_start_batch`.
4. For each candidate, call `search_start_agent_session`.
5. Launch a foreground Agent using the returned launch payload:
   - agent type: `launch.agent_type`
   - message: `launch.message`
   - background: false
6. Keep workers in the foreground. If `launch.budget_control.mode == "host_turn_limit"`,
   verify that `launch.agent_type` names a Claude agent definition with a matching
   `maxTurns` frontmatter value before launch.
7. If the Agent result includes an agent id or reusable agent name, call `search_bind_agent_handle` with:
   - `host: "claude-code"`
   - `external_id`
   - `task_name` only when the client exposes a stable name instead of an id
8. Run final `search_run_verifier` from the main agent before selecting.
9. Use `search_select`, `search_report`, and `search_promote` when appropriate.

## Worker Budget Control

Claude Code worker runtime is controlled through foreground agent definitions.
Use `launch.agent_type` exactly as returned by the runtime:

- `any-search-agent-flash` has `maxTurns: 4`
- `any-search-agent` has `maxTurns: 8`
- `any-search-agent-deep` has `maxTurns: 16`

`budget_control.max_turns` documents the expected bound. The enforcement comes
from the selected Claude Code agent's `maxTurns` frontmatter. The runtime maps
known budgets 4, 8, and 16 to the matching agent types when `worker_agent_type`
is omitted.

## Continuation

If `search_continue_agent_session` returns a `SendMessage` payload, send the
message to the specified agent in the foreground. If no handle is bound, start
a new foreground Agent for the same candidate.
