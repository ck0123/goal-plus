# Codex Reference

This project can run `/goal-plus` from Codex. Search Mode uses the Search MCP
Runtime through the host adapter introduced for `strategy.worker_host =
"codex"`.

For the cross-host capability matrix and adapter contract, see
[agent-host-adapters.md](agent-host-adapters.md).

## Config

The tracked project-local MCP configuration template is:

```text
.codex/config.example.toml
.codex/hooks.json
.codex/skills/goal-plus/SKILL.md
.codex/skills/goal-plus-with-final-check/SKILL.md
.codex/skills/search/SKILL.md
.codex/agents/search_candidate_agent.toml
.codex/agents/goal_plus_final_checker.toml
scripts/hooks/goal_plus_stop.py
```

Create the ignored local config before using Codex in this checkout:

```bash
cp .codex/config.example.toml .codex/config.toml
```

Keeping `.codex/config.toml` untracked lets each checkout set local approval or
runtime options without changing the repository template.

Use `goal-plus` as the user-facing skill. The `search` skill is the internal
Search Mode engine after Goal Plus has frozen and, when needed, confirmed a
verifier-backed spec.

This repository ships project-local Goal Plus host hooks for Codex 0.144.1 and
newer. `.codex/hooks.json` runs
`goal-plus --goal-plus-host-hook` for `UserPromptSubmit`,
`SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, and `SubagentStop`. Codex
project hooks must be reviewed and trusted through `/hooks` before they run.

`UserPromptSubmit` pre-creates and binds `/goal-plus` or `$goal-plus` before the
model turn, while `SessionStart` restores hidden context for an active bound
goal. `PreToolUse` gates Search tools and mutating built-ins.
`PostToolUse(goal_plus_create)` remains the compatibility binding path when the
pre-model hook was unavailable. Subagent tool events are ignored for ownership
binding. `Stop` and `SubagentStop` gate only an explicitly selected
`GOAL_PLUS_ID` or an active Goal Plus record whose bound session matches the
current Codex session. The skill continues to record explicit gate calls for
auditability.

The same hook recognizes `/goal-plus edit <full revised goal>`,
`/goal-plus resume`, and `/goal-plus-with-final-check <goal>`. Edit updates the
bound record in place,
increments `goal_revision`, and injects the revised objective before the next
model turn. With-check creation stores `policy.final_check.mode="required"`.
Those commands are fallbacks rather than the only continuation path. The
hidden context and skill require Codex to interpret each latest user message:
keep the revision for continuation or implementation steering, call
`goal_plus_update_goal` and re-triage when scope/deliverables/success criteria
change, and clarify unrelated or ambiguous intent instead of automatically
resuming merely because a record is active.
At completion, Codex calls `goal_plus_prepare_final_check(checker_host="codex")`
and launches the returned `spawn_agent` payload foreground with
`fork_turns="none"`. The reviewer, not the parent, submits the verdict with
`goal_plus_submit_final_check`. Repeated preparation is idempotent while a
check remains pending; an objective edit supersedes it. If a final-check
subagent stops without submitting a verdict, `SubagentStop` records the attempt
as interrupted and allows the parent to launch a fresh checker.

Set `GOAL_PLUS_ID=gp_...` to force the hook to gate a specific active goal when
multiple Goal Plus records are active. Set `GOAL_PLUS_STOP_HOOK_DISABLED=1` or
`GOAL_PLUS_HOST_HOOK_DISABLED=1` to temporarily bypass the hooks.

The MCP server is configured as:

```toml
[mcp_servers.gp-runtime]
command = "goal-plus"
args = ["--root", ".gp"]
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
  "agent_type": "search_candidate_agent",
  "fork_turns": "none",
  "message": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

The main Codex agent should project this adapter payload onto the `spawn_agent`
schema exposed by the current session, then record the returned task name or
nickname with `search_bind_agent_handle`. Always use `task_name`, `message`,
and the applicable fork field. Pass optional `agent_type`, `model`,
`reasoning_effort`, and `service_tier` only when the tool schema exposes them.
Codex configurations with `multi_agent_v2.hide_spawn_agent_metadata=true`
intentionally hide those optional fields; in that mode a child inherits the
parent model, and omitted launch metadata is not an adapter failure.

Optional native launch controls live under `strategy.worker_launch` and flow
to the returned adapter payload. The main agent passes each control only when
the current `spawn_agent` schema exposes it:

```json
{
  "strategy": {
    "worker_host": "codex",
    "worker_launch": {
      "model": "gpt-5.6-terra",
      "reasoning_effort": "high",
      "service_tier": "priority"
    }
  }
}
```

They are host launch choices, not runtime search state. Omit them to inherit
the parent Codex configuration.

For the regular Codex CLI, use the model slug shown by `/model`, such as
`gpt-5.6-terra`. No additional provider override is required.

## Worker And Orchestrator Boundary

Every Codex launch message states that the child is a candidate worker, not the search orchestrator.
It must call `search_get_agent_context`, work only in its candidate workspace,
and call `search_run_verifier`. It must not call `search_plan_next`,
`search_start_batch`, `search_select`, `search_report`, `search_promote`, or any
`goal_plus_*` tool.

This rule exists in both `.codex/agents/search_candidate_agent.toml` and the adapter
message. The duplication is intentional: when the current `spawn_agent` schema
hides `agent_type`, Codex launches a default child rather than the named local
agent, so the launch message must preserve the same ownership boundary.

## Verified Codex Cycle

The opt-in `codex_circle_packing_cycle` ST runs the ordinary Codex CLI with
`gpt-5.6-terra` and verifies two batches of two candidates:

```bash
python -m pytest -m "st and st_codex" -k codex_circle_packing_cycle -v -s -rs
```

The strict report contract requires `c001` through `c004`, four distinct
`agent_session_id` values, `rounds: 2`, `batch_sizes: [2, 2]`, at least one
verifier iteration per candidate, and a runtime-generated selection/report.
This proves the portable `random` multi-round path; it does not enable the
OpenCode-only high-touch strategies listed above.

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
    "initial_wait_timeout_ms": 555000,
    "soft_closeout_seconds": 45,
    "closeout_tool": "send_message",
    "closeout_target": "search_agent_001",
    "closeout_message": "Worker deadline is approaching. Stop starting new work, run one final search_run_verifier if needed, write .tmp/handoff.json, and return a concise summary.",
    "final_wait_timeout_ms": 45000,
    "on_exceed": "interrupt",
    "interrupt_tool": "interrupt_agent",
    "interrupt_target": "search_agent_001",
    "max_turns_hint": 8
  }
}
```

The parent Codex agent first waits for `initial_wait_timeout_ms`. On timeout it
sends the single `closeout_message`, giving the worker a chance to final-verify,
write `.tmp/handoff.json`, and return. It then waits for
`final_wait_timeout_ms`; only a second timeout triggers `interrupt_agent`.
This is a two-stage host watchdog, not a runtime-owned wait loop.

`max_turns` is only a hint for Codex workers. The enforceable control is
`max_runtime_seconds`.

The `PostToolUse` hook adds a separate informational timing check inside Search
candidate subagents. The first
`search_get_agent_context(agent_session_id)` PostTool event maps the native
subagent identity to the validated runtime session without binding Goal Plus
main-session ownership. Later PostTool events compare available time with the
verifier-count-weighted average of each candidate's elapsed time from earliest
session to latest subagent verifier. If one average submission no longer fits,
the hook injects one `additionalContext` advisory with the concrete candidate
timings. Main-agent, ordinary-subagent, and final-checker events are ignored;
the advisory never stops the worker. Set `GOAL_PLUS_OUTER_DEADLINE_AT` to an
RFC 3339 timestamp or Unix epoch when an outer benchmark deadline is available;
otherwise the session's worker budget is used. Delivery evidence stays under
ignored `.gp/host-logs/codex-time-advisory/` and is summarized by
`goal_plus_monitor_snapshot`.

If a worker is interrupted before it records any verifier iteration or usable
score, treat the budget as too small for that task. For later planned work,
call `search_redispatch_candidate` for the same candidate with a larger
`worker_budget.max_runtime_seconds`. You may also override `worker_agent_type`
when local Codex agent variants exist, but the enforceable control remains the
parent watchdog because Codex does not expose a hard per-subagent step tier.

Codex does not expose an equivalent same-worker continuation in this adapter.
When continuation is needed, call `search_redispatch_candidate` to start a new
foreground worker for the same candidate and use `search_get_agent_context` to
recover the authoritative state. Redispatch creates a fresh `agent_session_id`
for the same candidate workspace and may override `worker_agent_type` or
`worker_budget` for that launch. The worker should use `context.history` and
`context.iterations` from the MCP runtime; there is no `plan.md` history file
for Search Mode.

Before either a normal return or a deadline closeout, the worker writes a
bounded `.tmp/handoff.json`. A fresh redispatched worker recovers from the same
candidate workspace plus runtime-owned `context.history`,
`context.iterations`, Git state, and verifier evidence; it does not depend on
the previous chat transcript.

## Debugging Logs

For the cross-host debugging workflow, see
[debugging-runtime.md](debugging-runtime.md).

Codex has two useful log surfaces for this adapter:

- `codex exec --json ... > .gp/host-logs/codex-<timestamp>.jsonl` captures
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
