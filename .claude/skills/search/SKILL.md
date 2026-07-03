---
name: search
description: Run agentic any-search with foreground Claude Code agents and the search-runtime MCP server.
---

# Search Runtime for Claude Code

Use this skill when the user asks to run or continue an agentic search.

Use the logical `search_*` tools exposed by the `search-runtime` MCP server.
Claude Code may display MCP tools with a server prefix; match by the final
logical tool name.

## Main Workflow

1. Call `search_freeze_spec` or `search_create`.
2. Call `search_plan_next`.
3. Call `search_start_batch`.
4. For each candidate, call `search_start_agent_session`.
5. Launch a foreground Agent using the returned launch payload:
   - agent type: `launch.agent_type`
   - message: `launch.message`
   - background: false
6. Keep workers in the foreground.
7. If the Agent result includes an agent id or reusable agent name, call `search_bind_agent_handle` with:
   - `host: "claude-code"`
   - `external_id`
   - `task_name` only when the client exposes a stable name instead of an id
8. Run final `search_run_verifier` from the main agent before selecting.
9. Use `search_select`, `search_report`, and `search_promote` when appropriate.

## Continuation

If `search_continue_agent_session` returns a `SendMessage` payload, send the
message to the specified agent in the foreground. If no handle is bound, start
a new foreground Agent for the same candidate.

