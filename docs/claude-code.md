# Claude Code Reference

This project can run `/goal-plus` from Claude Code. Search Mode uses the Search
MCP Runtime through the host adapter introduced for `strategy.worker_host =
"claude-code"`.

For the cross-host capability matrix and adapter contract, see
[agent-host-adapters.md](agent-host-adapters.md).

## Compatibility Snapshot

The local version verified during the original adapter design was:

```text
2.1.142 (Claude Code)
```

This is an evidence snapshot, not a minimum or latest-version claim. The
adapter intentionally relies only on the checked-in and tested subset:
foreground Agent launches, bounded custom agents, and optional `SendMessage`
continuation when the active Claude Code surface exposes a reusable agent
handle.

This repository ships Claude Code Goal Plus host hooks:
`.claude/settings.json` runs `goal-plus --goal-plus-host-hook`
for `PostToolUse` and `Stop`.

## Config

Project-local assets:

```text
.mcp.json
.claude/settings.json
.claude/skills/goal-plus/SKILL.md
.claude/skills/search/SKILL.md
.claude/agents/search-candidate-agent.md
.claude/agents/search-candidate-agent-flash.md
.claude/agents/search-candidate-agent-deep.md
scripts/hooks/goal_plus_stop.py
```

Use `goal-plus` as the user-facing skill. The `search` skill is the internal
Search Mode engine after Goal Plus has frozen and, when needed, confirmed a
verifier-backed spec.

The MCP server is configured as:

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

## Goal Plus Hook Status

Claude Code assets provide Search Mode worker orchestration, manual Goal Plus
gate calls through the `goal-plus` skill, and session-scoped Goal Plus host
hooks.

Concretely, the checked-in assets wire:

- a `PostToolUse(goal_plus_create)` hook that binds the created Goal Plus
  record to the current top-level Claude Code `session_id`
- a `Stop` hook that calls `goal-plus --goal-plus-host-hook`

Subagent tool events are ignored for ownership binding. The Stop hook gates
only an explicitly selected `GOAL_PLUS_ID` or an active Goal Plus record whose
bound session matches the current Claude Code session.

The project does not wire PreToolUse or SubagentStop hooks. The agent must
still call `goal_plus_gate(event="pre_tool_use", ...)` before Search Mode tools
and call the stop gate manually before the final response. The hook exists to
catch a missed final stop gate, not to supervise foreground workers.

Set `GOAL_PLUS_ID=gp_...` to force the hook to gate a specific active goal when
multiple Goal Plus records are active. Set `GOAL_PLUS_STOP_HOOK_DISABLED=1` or
`GOAL_PLUS_HOST_HOOK_DISABLED=1` to temporarily bypass the hooks.

## Claude Code Parity Assessment

Claude Code is already a usable Search Mode worker host, but it is not at Pi's
current Goal Plus integration level. The largest gap is not the common runtime
adapter: both hosts use the same Search Mode records, candidate workspaces,
verifier APIs, handle binding, history, selection, and reporting. The gap is in
the host-native integration surrounding that adapter.

Keep these three states separate when evaluating parity:

- **Implemented** means the repository ships the asset or code path and covers
  it with local tests.
- **Host-capable** means current Claude Code documentation exposes the required
  primitive, but this repository has not connected or tested it.
- **Conditional** means availability depends on the Claude Code surface,
  session, or an experimental feature; it is not a portable adapter guarantee.

| Area | Repository status | Claude Code host capability | Remaining gap |
|---|---|---|---|
| Common Search Mode runtime | Implemented | MCP tools and foreground subagents are available | No material Pi-specific runtime gap |
| Foreground worker launch | Implemented with `Agent` and `background: false` | Custom subagents support foreground execution | No material gap |
| Worker budget | Implemented with `maxTurns` tiers 4, 8, and 16 | Custom subagents expose `maxTurns` | No material gap, but this is a turn limit rather than Pi's wall-clock watchdog |
| Goal creation and ownership binding | Partially implemented through `PostToolUse(goal_plus_create)` | `UserPromptSubmit`, `UserPromptExpansion`, and `SessionStart` hooks can inject or restore context | Exact `/goal-plus` pre-creation and resumed-session restoration are not wired |
| Pre-tool lifecycle gate | Manual skill call | `PreToolUse` can allow, deny, or rewrite a tool call before execution | No checked-in `PreToolUse` hook or tests |
| Top-level stop gate | Implemented with the session-scoped `Stop` hook | `Stop` can block completion | Substantially covered |
| Subagent stop gate | Manual/instruction-driven | `SubagentStop` can block a subagent from stopping | No checked-in `SubagentStop` hook or tests |
| Worker-start context | Worker calls `search_get_agent_context` explicitly | `SubagentStart` can inject additional context | No host hook that verifies or injects the candidate context at launch |
| Candidate isolation | Runtime creates candidate workspaces and the worker prompt restricts edits | Custom subagents also expose optional `isolation: worktree` | Current assets rely on instruction plus runtime workspace paths; native worktree isolation is not validated and must not be enabled without checking verifier paths and promotion semantics |
| Same-worker continuation | Conditional adapter payload | A stopped subagent can expose an id, but messaging/resume support varies by surface and feature configuration | Keep `search_redispatch_candidate` as the portable recovery path |
| Batch driver | Main-agent skill manually performs the control-plane sequence | Claude Code can launch multiple subagents | No Claude-native equivalent of `pi_search_run_batch` with normalized per-step evidence |
| Token and cost evidence | Host-native logs only; not bound into Search Mode metadata | Headless JSON exposes cost fields; stream JSON exposes usage and session events | No parser/driver currently records those fields on the bound handle or monitor snapshot |
| Trace export | Not implemented | Transcripts and debug streams are available | No repository trace exporter equivalent to the OpenCode exporter |
| Strategy coverage | Portable builtin subset only | The host can run the same foreground worker shape for more builtin planners | Each additional strategy still needs payload tests and a real smoke path before validation is relaxed |

The practical result is:

- Claude Code is close to Pi for the worker execution core: foreground launch,
  bounded work, MCP context recovery, verifier execution, and state-level
  redispatch already exist.
- Claude Code is behind Pi in repository-owned lifecycle coverage and
  observability: prompt-time creation, session restoration, pre-tool and
  subagent-stop enforcement, batch-driver evidence, and normalized cost metrics
  are not connected.
- Claude Code has host primitives that could exceed the current Pi integration
  in some areas, notably a blocking `SubagentStop` hook and optional native
  worktree isolation. Those are opportunities, not current support claims.

As a planning count, the table contains four directly covered areas, five
partial or conditional areas, and five clear repository gaps. This is not a
weighted parity score: missing lifecycle enforcement is more important than a
missing trace exporter.

Claude Code's relevant host primitives are documented in the official
[hooks reference](https://code.claude.com/docs/en/hooks),
[subagents reference](https://code.claude.com/docs/en/sub-agents), and
[headless reference](https://code.claude.com/docs/en/headless).

## Claude Code-Native Completion Plan

The completion path should remain Claude Code-native. Do not introduce a
runtime-owned worker supervisor or make Search Mode state depend on Claude
transcripts.

### Priority 0: lifecycle parity

1. Add an exact Goal Plus prompt path using `UserPromptSubmit` and, where
   required for direct skill expansion, `UserPromptExpansion`. Pre-create and
   bind the record before model reasoning, while retaining
   `PostToolUse(goal_plus_create)` as a compatibility fallback.
2. Add `SessionStart` restoration for the active session-bound Goal Plus record.
   Inject only compact hidden ownership/phase context; durable state remains in
   `.gp`.
3. Wire `PreToolUse` to the existing Goal Plus gate for `search_*` and mutating
   tools. A blocking hook result must be covered by asset tests and an actual
   Claude Code smoke before it is described as enforced.
4. Wire `SubagentStop` to the stop gate, preserving Claude Code's documented
   stop-hook loop protection. Keep the top-level `Stop` backstop.

These changes belong in Claude settings/assets and the shared host-hook
facade. They do not require lifecycle fields, wait loops, or abort APIs in the
runtime.

### Priority 1: execution evidence and recovery

1. Add a Claude-native batch convenience driver only if it can remain a thin
   composition over `search_start_agent_session`, foreground `Agent`, binding,
   and final verification. It must not plan, select, report, or promote.
2. Parse headless stream/JSON usage and cost fields into bounded host-handle
   metadata, using the same monitor-facing shape where it is genuinely
   host-neutral. Preserve raw Claude logs outside committed state.
3. Test same-worker messaging as a conditional optimization on the supported
   Claude surface. Do not make it the recovery contract: a fresh worker plus
   `search_redispatch_candidate`, `context.history`, and `context.iterations`
   remains authoritative.
4. Add a real smoke that proves a worker starts in and edits only the assigned
   candidate workspace. Evaluate native `isolation: worktree` separately;
   enabling it changes Git/workspace behavior and is not a documentation-only
   switch.

### Priority 2: coverage expansion

1. Enable additional builtin strategies one at a time after launch-payload,
   lineage/context, and two-round smoke coverage exists.
2. Add a Claude transcript/debug-stream exporter only when a consumer needs
   normalized trace data; native logs remain the first debugging source.
3. Add host-specific system-test markers, for example `pytest.mark.claude`,
   if real Claude Code tests are introduced. Existing asset and adapter unit
   tests should remain part of the default suite; a host mark must identify an
   opt-in external-host test, not hide ordinary contract coverage.

Completion should be claimed only after all three evidence layers agree:

1. adapter/unit tests prove payload and capability mapping
2. asset tests prove checked-in hook, skill, and agent wiring
3. at least one real Claude Code smoke proves the host behavior that mocks
   cannot establish

## Supported Strategies

Claude Code currently supports the portable builtin strategies only:

- `agent_guided`, `agent`, or `default`
- `random` or `random_mode`

OpenCode-specific or high-touch strategies such as `openevolve`, `evolve`,
`mcts`, Python strategy plugins, and external strategy drivers remain
OpenCode-only until they are adapted and tested.

## Worker Flow

After `/goal-plus` enters Search Mode, the runtime returns a Claude Code
foreground launch payload from
`search_start_agent_session`:

```json
{
  "tool": "Agent",
  "agent_type": "search-candidate-agent",
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
    "worker_agent_type": "search-candidate-agent-deep",
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
| `search-candidate-agent-flash` | 4 |
| `search-candidate-agent` | 8 |
| `search-candidate-agent-deep` | 16 |

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

Choose `search-candidate-agent-flash` only for smoke tests or cheap probes. Use the
default worker for normal candidate work. Use `search-candidate-agent-deep` when the
source tree is large, the verifier is slow, the edit requires cross-file
reasoning, or a flash worker reaches `maxTurns` before recording any verifier
iteration or usable score.

Claude Code Agent results may include an `agentId` with a `SendMessage` hint,
but the `claude -p` tool surface used by this adapter may not expose a usable
`SendMessage` tool. Treat same-agent continuation as conditional and unverified
unless a real run shows the tool call succeeding. If no handle is bound,
`SendMessage` is unavailable, or a larger turn budget is needed, call
`search_redispatch_candidate` for the same candidate with a matching
`worker_agent_type` / `worker_budget.max_turns`. The new worker should use
`search_get_agent_context` to recover the authoritative state from
`context.history` and `context.iterations`; there is no `plan.md` history file
for Search Mode.

## Debugging Logs

For the cross-host debugging workflow, see
[debugging-runtime.md](debugging-runtime.md).

For reproducible adapter runs, capture both stream output and debug logs:

```bash
mkdir -p .gp/host-logs
claude -p --verbose --output-format stream-json \
  --debug-file .gp/host-logs/claude-debug.log \
  "<search prompt>" \
  > .gp/host-logs/claude.jsonl
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
