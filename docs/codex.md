# Codex

Codex is the native multi-agent host for Goal Plus. It uses the shared MCP
runtime and a rolling pool built from Codex collaboration tools.

## Setup

```bash
python -m pip install -e ".[dev]"
cp .codex/config.example.toml .codex/config.toml
```

The local config starts:

```toml
[mcp_servers.gp-runtime]
command = "goal-plus"
args = ["--root", ".gp"]
startup_timeout_sec = 10
tool_timeout_sec = 300
enabled = true
```

Keep `.codex/config.toml` untracked and omit MCP `cwd`; `codex -C` should decide
the project root for both MCP state and hooks.

Codex 0.144.1+ ships project-local Goal Plus host hooks in
`.codex/hooks.json`; each calls `goal-plus --goal-plus-host-hook` for
`UserPromptSubmit`, `SessionStart`, `PreToolUse`,
`PostToolUse`, `Stop`, and `SubagentStop`. Review and trust them through
`/hooks`. They pre-create `/goal-plus`, restore active state, gate writes/Search
calls, bind ownership, and keep a candidate alive only until its own verifier
submission is durable. Selection and final audit remain parent-owned.
`PostToolUse(goal_plus_create)` is the fallback ownership-binding path. A
candidate is blocked until its own verifier submission is durable. Ordinary subagents do not inherit the parent's next action.

Use `/goal-plus mode=autonomous <goal>` for substantial renewable candidate
exploration (the default), or `/goal-plus mode=probe <goal>` for short
feasibility/potential/blocker probes. The runtime stores the choice only as the
canonical final line of `raw_goal`.

Top-level `Stop` blocks every still-active record and re-presents the full raw
goal, timestamps, elapsed time, phase, next action, and final-check policy. The
main agent must continue or record a terminal status. Candidate `SubagentStop`
semantics are unchanged.

Use the `goal-plus` skill as the user entry. The `search` skill is internal.

## Rolling Worker Flow

After planning and materializing candidates, `search_start_agent_session`
returns a launch payload like:

```json
{
  "tool": "spawn_agent",
  "task_name": "search_agent_001",
  "fork_turns": "none",
  "message": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

Project it onto the current tool schema. `task_name`, `message`, and the fork
field are stable. The default candidate-worker contract is self-contained in
`message`, so the child uses Codex's native parent-model inheritance without a
project role reload. Pass a non-default `agent_type`, model, reasoning, or
service tier only when explicitly requested by `strategy.worker_launch` and
exposed by the current tool schema.

The parent then:

1. launches up to `budget.max_parallel` workers;
2. binds returned handles with `search_bind_agent_handle`;
3. calls targetless `wait_agent` and inspects `list_agents` after each wake;
4. final-verifies every newly terminal candidate immediately;
5. refills a free slot or continues a valuable worker with
   `search_continue_agent_session` followed by `followup_task`;
6. drains live agents before selection.

Every launch message says the child is a candidate worker, not the search orchestrator.
It may read its context, edit its candidate workspace, verify, and return a
research handoff. It must not plan, select, report, promote, or mutate Goal Plus
state.

## Worker Deadline

Codex has no spawn-time step or timeout field. `worker_budget` therefore
requires `max_runtime_seconds`; the adapter returns a per-dispatch two-stage
parent watchdog:

```json
{
  "budget_control": {
    "initial_wait_timeout_ms": 555000,
    "closeout_message": "Stop new work, final-verify, write the handoff, and return.",
    "final_wait_timeout_ms": 45000,
    "on_exceed": "interrupt"
  }
}
```

The parent waits until the closeout point, sends one message, waits the final
window, and interrupts only after the second timeout. `max_turns` is a hint,
not an enforceable Codex cap. A continuation may receive a larger one-dispatch
budget without changing the frozen spec.

The `PostToolUse` hook may also inject one informational timing advisory when
the remaining outer/worker time is below observed verifier-submission time.
It never stops the worker. Evidence is visible through
`goal_plus_monitor_snapshot`.

## Resume

Prefer native continuation while the Codex worker remains available:

```text
search_continue_agent_session -> followup_task
```

If the worker is gone or a fresh context is intentional, use
`search_redispatch_candidate`. The new session uses the same workspace, Git
state, verifier history, and structured `.tmp/handoff.json`.

## Supported Strategies

Codex supports the portable builtin set:

- `agent_guided`, `agent`, `default`
- `random`, `random_mode`

See [Agent Host Adapters](agent-host-adapters.md) before enabling another
strategy.

## Verification

Fast contract tests:

```bash
pytest -m codex -q
```

Real Codex paths use the CLI model slug `gpt-5.6-terra` by default:

```bash
pytest -m "st and st_codex" -k codex_circle_packing_cycle -v -s -rs
pytest -m "st and st_codex" -k codex_rolling_followup -v -s -rs
```

`codex_circle_packing_cycle` proves the portable 2 x 2 Search cycle.
`codex_rolling_followup` proves wait-any behavior and same-worker follow-up
while another worker remains live.

## Logs

```bash
codex exec --json ... > .gp/host-logs/codex-run.jsonl
RUST_LOG=debug codex -c log_dir=./.codex-log
```

Persisted transcripts are under
`${CODEX_HOME:-~/.codex}/sessions/YYYY/MM/DD/rollout-*.jsonl`. Use JSON or
rollout logs when you need tool calls; `codex exec -o` contains only the final
message. Cross-host diagnosis is documented in
[Debugging](debugging-runtime.md).
