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
4. For each new candidate, call `search_start_agent_session`.
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
8. If a worker reaches `maxTurns` before useful verifier evidence, call
   `search_redispatch_candidate(run_id, candidate_id, directive?,
   worker_agent_type="any-search-agent-deep",
   worker_budget={"max_turns": 16, "on_exceed": "interrupt"})` and launch the
   returned foreground Agent payload for the same candidate workspace.
9. Run final `search_run_verifier` from the main agent before selecting.
10. Use `search_select`, `search_report`, and `search_promote` when appropriate.

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

Choose the initial tier before freezing the spec:

- Use `any-search-agent-flash` only for smoke tests or very cheap probes.
- Use `any-search-agent` for normal candidate work.
- Use `any-search-agent-deep` when the source tree is large, the verifier is
  slow, the edit requires cross-file reasoning, or a previous flash worker
  reached `maxTurns` before recording any verifier iteration or usable score.

If a worker returns no useful verifier evidence because the tier was too small,
prefer a higher tier for later planned work or a replacement search run. Do not
repeat the same underpowered tier unless the user explicitly wants a cheap
probe.

## Runtime History And Resume

History is runtime-owned, not a `plan.md` file. The main agent reads prior
candidate results through `search_list_history`; workers recover state through
`search_get_agent_context`, which returns `context.history` and
`context.iterations`.

For hosts or tool surfaces that cannot re-enter the same foreground agent, use
state-level resume: call `search_redispatch_candidate` to start a new
foreground Agent for the same candidate workspace, optionally overriding
`worker_agent_type` and `worker_budget.max_turns`. The returned prompt tells
the worker to treat `search_get_agent_context` as the authoritative resume
context. Do not ask the worker to infer prior attempts from chat transcript.

## Continuation

If `search_continue_agent_session` returns a `SendMessage` payload and the
current Claude Code tool surface actually exposes a usable `SendMessage` tool,
send the message to the specified agent in the foreground. If no handle is
bound, `SendMessage` is unavailable, or the host cannot prove same-agent
continuation, call `search_redispatch_candidate` and rely on MCP
history/iterations for resume.
