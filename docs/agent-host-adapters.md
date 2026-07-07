# Agent Host Adapters

`agentic-any-search-mcp` is host-neutral at the runtime layer. `/goal-plus`
owns goal intake, triage, spec drafts, verifier confirmation, and final audit
state. The internal Search Mode engine owns durable search state, workspaces,
verifier execution, scoring history, reports, and promotion patches. The
selected code-agent host owns worker process lifecycle.

The adapter layer is the small boundary between those two concerns. It converts
one runtime concept, an `AgentSessionRecord`, into the host-native foreground
worker call that the main agent should execute.

## When To Use This Page

Use this page when `/goal-plus` has upgraded a task to Search Mode and you need
to choose a host, write a `SearchSpec` with `strategy.worker_host`, or add a new
host adapter without changing runtime state semantics.

Host setup references:

- [OpenCode](opencode.md)
- [Codex](codex.md)
- [Claude Code](claude-code.md)
- [Runtime and host log debugging](debugging-runtime.md)

---

## Common Runtime Contract

After `/goal-plus` has frozen or linked a verifier-backed SearchSpec, all three
hosts use the same Search Mode MCP control plane:

1. `search_freeze_spec`
2. `search_create`
3. `search_plan_next`
4. `search_start_batch`
5. `search_start_agent_session`
6. host foreground worker launch
7. `search_bind_opencode_session` or `search_bind_agent_handle`
8. worker `search_get_agent_context`
9. worker `search_run_verifier(..., agent_session_id=...)`
10. main-agent final `search_run_verifier(...)`
11. `search_select`
12. `search_report`

The runtime does not wait for, abort, supervise, or synchronize host workers.
It records provenance and verifier counters only after the host or worker calls
the corresponding MCP tools.

## Goal Plus Enforcement Levels

Do not conflate Search Mode worker support with enforced Goal Plus lifecycle
control.

Search Mode support means a host can launch foreground candidate workers and
the runtime can record verifier-backed search results. Goal Plus lifecycle
enforcement means a host hook or skill call checks Goal Plus phase state at
checkpoints such as:

- before `search_*` tools that create or run search state
- before the top-level agent stops
- before a subagent stop, if the host exposes that hook

Current repository assets include Goal Plus host hooks for Codex and Claude
Code. Host settings run `agentic-any-search-mcp --goal-plus-host-hook`.
`PostToolUse(goal_plus_create)` binds the created Goal Plus record to the
current top-level host `session_id`; subagent tool events do not bind
ownership. `Stop` then reads local `.search/goal-plus` state and applies the
same `goal_plus_gate(event="stop")` semantics only to an explicit
`GOAL_PLUS_ID` or a record whose bound session matches the current host
session. If that record still has a required next action, the hook returns a
host-native block decision with the continuation prompt.

OpenCode still has no shipped hook. No host currently has a shipped
`PreToolUse` or `SubagentStop` hook. Those gate calls remain manual /
instruction-driven in the skills, so this is session-scoped Stop backstop plus
ownership binding rather than full process supervision.

## Host Selection

Set `strategy.worker_host` in the `SearchSpec`:

```json
{
  "strategy": {
    "name": "random",
    "driver": "builtin",
    "worker_mode": "agent-session-pool",
    "worker_host": "codex",
    "worker_agent_type": "any_search_agent"
  }
}
```

Valid host values are:

- `opencode`
- `codex`
- `claude-code`

If `worker_host` is omitted, the runtime defaults to `opencode`.

---

## Current Host Differences

| Capability | OpenCode | Codex | Claude Code |
|---|---|---|---|
| Config files | `opencode.json`, `.opencode/` | `.codex/config.toml`, `.agents/skills/goal-plus/`, `.agents/skills/search/`, `.codex/agents/` | `.mcp.json`, `.claude/skills/goal-plus/`, `.claude/skills/search/`, `.claude/agents/` |
| Default worker agent type | `AnySearchAgent` | `any_search_agent` | `any-search-agent` |
| Launch tool | `Task` | `spawn_agent` | `Agent` |
| Worker mode | foreground Task | foreground spawned agent | foreground Agent, `background: false` |
| Bind tool | `search_bind_opencode_session` | `search_bind_agent_handle` | `search_bind_agent_handle` |
| Bound handle | OpenCode `metadata.sessionId` | task name, nickname, or returned agent id when available | reusable agent id/name when available; nickname otherwise |
| Same-worker continuation | supported with `Task(task_id=...)` | not supported by this adapter | conditional; Agent results may expose an id, but `SendMessage` is not reliable on every `claude -p` tool surface |
| Host-native debug evidence | OpenCode DB/log plus `.search` state | `codex exec --json`, `$CODEX_HOME/sessions` rollouts, optional TUI log | `claude -p --output-format stream-json`, `--debug-file`, `~/.claude/projects` transcripts |
| Trace export | supported for OpenCode logs | not implemented | not implemented |
| Goal Plus gate enforcement | manual skill/orchestrator calls; no Stop/PreToolUse hook shipped | PostToolUse session binding, session-scoped Stop hook; PreToolUse/SubagentStop manual | PostToolUse session binding, session-scoped Stop hook; PreToolUse/SubagentStop manual |
| Strategy coverage | baseline host; all existing OpenCode-tested strategies | portable builtin strategies only | portable builtin strategies only |

Portable builtin strategies are:

- `agent_guided`
- `agent`
- `default`
- `random`
- `random_mode`

OpenCode remains the compatibility baseline for high-touch or host-specific
behavior such as Python strategy plugins, external proposal drivers,
OpenEvolve-style workflows, MCTS-style workflows, and project trace export.
Codex and Claude Code have usable host-native transcripts and debug logs, but
the project has not implemented equivalent trace exporters for them. Support
should be expanded one strategy at a time with explicit system tests.

## Single-Worker Autoresearch And Runtime Limits

Single-worker autoresearch is supported by all three host assets: the worker
receives an `agent_session_id`, calls `search_get_agent_context`, works inside
the assigned candidate workspace, runs `search_run_verifier`, and returns a
summary for the main agent to select from.

Runtime length control is not currently equivalent across hosts:

| Host | Single-worker autoresearch | Runtime cap exposed by current assets | What the cap controls |
|---|---|---|---|
| OpenCode | supported with the full `AnySearchAgent` loop | yes, `steps` in `.opencode/agents/*.md` | host step budget per Task; current tiers are 15, 50, 100, and 150 steps |
| Codex | supported with the project Codex worker prompt | yes, through `worker_budget.max_runtime_seconds` and a parent watchdog | parent waits with `wait_agent(timeout_ms=...)`, then interrupts the child if the deadline expires |
| Claude Code | supported with the project Claude worker prompt | yes, through `worker_budget.max_turns` and bounded `.claude/agents/*.md` definitions | host turn budget per foreground Agent; current tiers are 4, 8, and 16 turns |

`budget.max_candidates`, `budget.max_parallel`, and strategy round settings
control how many workers the runtime plans. They do not bound how long an
individual host worker thinks or edits once launched.

Use `strategy.worker_budget` for host-neutral worker limits:

```json
{
  "strategy": {
    "worker_host": "codex",
    "worker_budget": {
      "max_runtime_seconds": 600,
      "max_turns": 8,
      "on_exceed": "interrupt"
    }
  }
}
```

OpenCode continues to use worker agent tiers such as `AnySearchAgentFlash` and
`AnySearchAgentDeep`. Codex maps wall-clock budgets to a watchdog that waits
for activity or completion and interrupts the child after the deadline, because
`spawn_agent` itself does not accept a timeout argument. Depending on the Codex
multi-agent surface, interruption may be exposed as `interrupt_agent` or as
`send_input(..., interrupt=true)`. Claude Code maps turn budgets to `maxTurns`
in the selected local agent definition. When `worker_agent_type` is omitted,
Claude Code budgets of 4, 8, and 16 turns map to `any-search-agent-flash`,
`any-search-agent`, and `any-search-agent-deep` respectively.

For Codex, keep these controls distinct:

- `agents.max_depth` limits spawned-agent nesting depth. It is not a time or
  step budget.
- `agents.max_threads` limits concurrently open agent threads. It is not a
  worker runtime cap.
- `agents.job_max_runtime_seconds` applies to `spawn_agents_on_csv` jobs, not
  ordinary `spawn_agent` workers.
- Codex can stop a live spawned agent turn by agent id or canonical task name,
  so adapter-driven wall-clock deadlines are feasible even without a spawn-time
  timeout field.

Host-specific validation prevents unsupported budget shapes:

- Codex `worker_budget` requires `max_runtime_seconds`.
- Claude Code `worker_budget` requires `max_turns`.
- Known Claude Code agent types must match their configured `maxTurns`; custom
  Claude agent types are allowed when specified explicitly.

Main agents should choose worker size from task shape before freezing the spec.
Use cheap/flash tiers only for smoke probes. If a worker stops because the
selected tier was too small and it records no verifier iteration or usable
score, call `search_redispatch_candidate` for the same candidate and raise the
worker size for that dispatch: OpenCode raises `worker_agent_type`, Claude Code
raises `worker_budget.max_turns` / `worker_agent_type`, and Codex raises
`worker_budget.max_runtime_seconds` as the enforceable control. Codex may also
override `worker_agent_type` when local agent variants exist, but that is not a
hard step cap.

## State-Level Resume

Same-worker continuation is optional host sugar, not the portable recovery
model. The portable model is state-level resume:

1. Start a new host worker for the same candidate workspace when same-worker
   continuation is unavailable or unreliable by calling
   `search_redispatch_candidate(run_id, candidate_id, directive?,
   worker_agent_type?, worker_budget?)`.
2. The runtime returns a fresh `agent_session_id` and host launch payload for
   the same candidate workspace.
3. The worker calls `search_get_agent_context(agent_session_id)`.
4. The worker treats `context.history` and `context.iterations` as the
   authoritative prior-attempt record.
5. The main agent uses `search_list_history` and `search_list_iterations` for
   audit and follow-up planning.

Search history lives in the MCP runtime's `.search/runs/...` candidate records,
not in a `plan.md` file.

## Strategy Support Matrix

| Strategy or driver | OpenCode | Codex | Claude Code | Notes |
|---|---|---|---|---|
| `agent_guided`, `agent`, `default` | supported | supported | supported | proposal-based; main agent must pass proposals to `search_start_batch` |
| `random`, `random_mode`, `random-mode` | supported | supported | supported | fixed work orders; `search_start_batch` needs no proposals |
| `independent_branches` | supported | not supported | not supported | treated as OpenCode-only for now, even though it is builtin |
| `evolve` | supported | not supported | not supported | OpenCode-tested strategy behavior only |
| `openevolve` | supported | not supported | not supported | OpenCode-tested strategy behavior only |
| `mcts` | supported | not supported | not supported | OpenCode-tested strategy behavior only |
| Python strategy driver, including `adaptevolve` | supported | not supported | not supported | non-OpenCode hosts reject non-builtin drivers |
| `external_mcp` strategy driver | OpenCode-only boundary | not supported | not supported | requires explicit host adaptation before use outside OpenCode |

## Missing Strategy Completion Limits

Most missing strategy support is not blocked by the runtime planner. The
builtin planners already produce host-neutral plans, work orders, lineage, and
history. The remaining work is to prove that each host's foreground worker can
consume those plans reliably and to avoid leaking OpenCode-only worker semantics
into Codex or Claude Code.

| Area | Can Codex be completed? | Can Claude Code be completed? | Main limit | Recommended path |
|---|---|---|---|---|
| `independent_branches` | yes | yes | no lifecycle-specific dependency; currently blocked only by conservative validation | Open it first with unit tests and one smoke test per non-OpenCode host |
| `evolve` | yes | yes | runtime-selected parent and inspirations must be clearly visible in worker context | Add host matrix tests for lineage/work orders, then run a two-round smoke per host |
| `openevolve` | likely yes | likely yes | sampled parent/archive/inspiration context is larger and easier for workers to ignore | Add tests for sampled context shape and host launch payloads; smoke test with small `requested_k` |
| current `mcts` | likely yes | likely yes | current implementation is a best-score frontier placeholder, not a full UCB tree policy | Treat it like fixed-work-order lineage first; revisit if true tree-state continuation is added |
| Python driver | not as-is | not as-is | custom strategies can emit OpenCode-specific `worker_policy` and worker tier names | Add host capability validation or host-specific policy mapping before enabling |
| `adaptevolve` | needs design work | needs design work | uses OpenCode worker tiers such as `AnySearchAgentFlash`, `AnySearchAgentDeep`, and `AnySearchAgentExtraDeep` | Introduce host-neutral tiers like `fast`, `default`, `deep`, `extra_deep`, then map them per adapter |
| `external_mcp` driver | possible, but undefined | possible, but undefined | external planner ownership and MCP availability are not defined across hosts | Define who calls the external planner and how proposals are returned before enabling |
| same-worker continuation algorithms | limited | limited | Codex adapter has no same-worker continuation; Claude Code may expose an agent id but `SendMessage` is not reliable on every tool surface | Prefer state-level resume with new-worker redispatch; treat same-worker continuation as a host-specific optimization only after a real smoke test |
| trace-driven algorithms | not currently | not currently | trace export is only implemented for OpenCode logs | Add host trace exporters or keep these OpenCode-only |

In practice, the safe expansion order is:

1. Enable `independent_branches`.
2. Enable `evolve`, `openevolve`, and the current `mcts` planner with mock/unit
   coverage first.
3. Run one real two-round smoke for Codex and Claude Code on at least one
   non-`random` strategy.
4. Redesign worker tiers before enabling `adaptevolve`.
5. Define the external planner contract before enabling `external_mcp`.

---

## Adapter Responsibilities

Adapters live in `src/agentic_any_search_mcp/agent_hosts.py`.

Each adapter exposes:

- `name`: the runtime host id
- `capabilities`: bind, continuation, trace export, and background-worker flags
- `build_launch_payload(...)`: host-native fields returned by
  `search_start_agent_session`
- `build_continue_payload(...)`: host-native fields returned by
  `search_continue_agent_session`, or a clear unsupported-capability error

The runtime uses `get_agent_host_adapter(strategy.worker_host)` when it builds
launch payloads and worker policy. It uses `get_agent_host_adapter(session.host)`
when a previous session is continued.

Adapters must not own:

- candidate workspace creation
- verifier execution
- score aggregation
- budget accounting
- report generation
- promotion patch export

Those are runtime responsibilities and should stay host-neutral.

## Launch Payloads

OpenCode launch payload:

```json
{
  "subagent_type": "AnySearchAgent",
  "description": "c001 try alternate parser",
  "prompt": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

Codex launch payload:

```json
{
  "tool": "spawn_agent",
  "task_name": "search_agent_001",
  "agent_type": "any_search_agent",
  "fork_turns": "none",
  "message": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

Claude Code launch payload:

```json
{
  "tool": "Agent",
  "agent_type": "any-search-agent",
  "description": "c001 try alternate parser",
  "background": false,
  "message": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

The main agent should treat the returned `launch` object as authoritative.
Do not reconstruct it from local assumptions.

## Binding Handles

Binding records host-native identity after the foreground worker starts or
returns:

- OpenCode callers may keep using `search_bind_opencode_session`.
- Codex and Claude Code callers use `search_bind_agent_handle`.

Generic handle shape:

```json
{
  "host": "claude-code",
  "external_id": "optional-stable-agent-id",
  "task_name": "optional-stable-task-name",
  "nickname": "c001",
  "metadata": {
    "tool": "Agent",
    "background": false
  }
}
```

`AgentSessionRecord.host_handle` is for provenance and optional continuation.
It is not a lifecycle status object.

---

## Adding Another Host

Add a host only through the adapter boundary:

1. Add the host literal to `AgentHostKind`.
2. Add an adapter implementing `AgentHostAdapter`.
3. Register it in `_ADAPTERS`.
4. Add project-local host assets or setup docs.
5. Add unit tests for launch, bind, continuation, worker policy, and strategy
   validation.
6. Add at least one real two-round smoke test that proves:
   - round 1 records worker verifier provenance
   - round 2 plans from recorded history
   - a fresh worker can consume the previous candidate state

Do not add host-specific branches to verifier execution, candidate workspace
copying, scoring, report generation, or promotion unless the runtime contract
itself changes.
