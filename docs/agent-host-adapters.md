# Agent Host Adapters

`goal-plus` is host-neutral at the runtime layer. `/goal-plus`
owns goal intake, triage, spec drafts, autonomous verifier readiness, and final audit
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
- [Pi](pi.md)
- [Runtime and host log debugging](debugging-runtime.md)

---

## Common Runtime Contract

After `/goal-plus` has frozen a verifier-backed SearchSpec, all four hosts use
the same Search Mode MCP control plane for each search task:

1. `search_freeze_spec`
2. `search_create`
3. `goal_plus_link_search_run`
4. `search_plan_next`
5. `search_start_batch`
6. `search_start_agent_session`
7. host foreground worker launch
8. `search_bind_opencode_session` or `search_bind_agent_handle`
9. worker `search_get_agent_context`
10. worker `search_run_verifier(..., agent_session_id=...)`
11. main-agent final `search_run_verifier(...)`
12. `search_select` checks out the best committed iteration and final-verifies it
13. `search_report`, optional `search_promote`, and `goal_plus_record_search_result`

The final raw-goal audit may finish the Goal Plus task or repeat this control
plane with another frozen spec and `run_id`. This multi-task relationship is
host-neutral; hosts still own only their foreground worker lifecycle.

The runtime does not wait for, abort, supervise, or synchronize host workers.
It records provenance and verifier counters only after the host or worker calls
the corresponding MCP tools.

Codex and Pi additionally support required delivery review. The shared runtime
creates a revision-bound request with `goal_plus_prepare_final_check`; Codex
launches its returned foreground `spawn_agent` message, while Pi passes the
returned launch object to `pi_goal_plus_run_final_check`. Both reviewers submit
their own structured result through `goal_plus_submit_final_check`. This is a
host-owned foreground launch, not runtime process supervision. Goal edits and
interrupted checker attempts use the same durable Goal Plus state; interruption
requires a fresh check while preserving the active goal revision.

Pi main-agent assets expose `pi_search_run_batch` as the default host-native
driver for steps 5 through 10 across the candidate ids returned by
`search_start_batch`; `pi_search_run_candidate` is the single-candidate
fallback. Both still use the same runtime records and return step evidence;
they do not plan batches, select winners, write reports, or promote patches.

Codex and Pi Search candidate workers also have a one-shot informational time
advisory. At worker PostTool boundaries, the host reads durable candidate
session/iteration timestamps and warns when the available time is below the
observed average time per subagent verifier submission. Codex injects
`PostToolUse.additionalContext`; Pi RPC sends `steer` after
`tool_execution_end`. Both paths validate that the event belongs to a Search
candidate, ignore main/ordinary-subagent/final-checker events, and leave the
worker free to choose its response. `GOAL_PLUS_OUTER_DEADLINE_AT` optionally
provides an RFC 3339 or Unix-epoch outer deadline; the worker budget remains a
fallback.

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
Code. Host settings run `goal-plus --goal-plus-host-hook`.
Codex 0.144.1+ wires `UserPromptSubmit`, `SessionStart`, `PreToolUse`,
`PostToolUse`, `Stop`, and `SubagentStop`: it pre-creates and binds an exact
`$goal-plus` skill prompt, restores hidden session context, gates
Search/mutating tools, and gates both top-level and subagent stops. Top-level
Stop owns the global Goal Plus next action. SubagentStop is role-aware: Search
candidates are held only until their own verifier submission is recorded,
ordinary subagents do not inherit parent actions, and final-check reviewers
retain their independent gate. The hook
parser retains `/goal-plus` as a compatibility spelling for clients that
forward it as prompt text, but Codex CLI does not register that spelling as a
native slash command; users invoke the skill with `$goal-plus` or `/skills`.
`PostToolUse(goal_plus_create)` remains a compatibility binding path.
Terminal Stop emits a compact host `systemMessage` with non-LLM run counters.
All ownership-sensitive events select only an explicit `GOAL_PLUS_ID` or the
record bound to the current top-level session; candidate ownership is then
resolved from the durable Codex agent identity to Search `agent_session_id`
mapping.

OpenCode still has no shipped hook. Claude Code retains session-scoped Stop
backstops plus PostToolUse ownership binding; its `PreToolUse` and
`SubagentStop` gate calls remain manual / instruction-driven. Pi is different:
the project extension owns the native `/goal-plus` command for interactive/RPC
sessions and an equivalent pre-model input transform for print/JSON, pre-creates
the Goal Plus record, persists the
active `goal_plus_id` in interactive Pi custom session entries, keeps it in
memory for print/JSON invocations, injects hidden Goal Plus context
on `before_agent_start`, runs the pre-tool gate from `tool_call` for
`search_*`, explicitly exposed `pi_rpc_run_worker` debugging calls, and
mutating built-ins, then runs the turn-level stop gate from `agent_end`.
Completion statistics are emitted as a Pi custom entry/notification, not as an
LLM message. The checked-in prompt template is a fallback when the extension is
not loaded; correctness in normal Pi modes uses the native command. Pi has no host process Stop hook;
it also has no `SubagentStop` hook that can block closing the process.

Across hook-enabled hosts, `spec_discovery` deliberately allows host
inspection and editing tools. Search tools are still gated until a complete
high-confidence draft exists, but discovery can execute public CLI probes or
materialize an optional custom verifier. The MCP freeze tool and Pi extension
both expose the nested `SearchSpec` contract directly.

## Host Selection

Set `strategy.worker_host` in the `SearchSpec`:

```json
{
  "strategy": {
    "name": "random",
    "driver": "builtin",
    "worker_mode": "agent-session-pool",
    "worker_host": "codex",
    "worker_agent_type": "search_candidate_agent"
  }
}
```

Valid host values are:

- `opencode`
- `codex`
- `claude-code`
- `pi-rpc`

If `worker_host` is omitted, the runtime defaults to `opencode`.

## What The Unified Adapter Does

The adapters share one runtime contract; they do not pretend the host tools
have one identical call signature. The common layer owns:

- host capability publication and validation
- creation of a foreground launch payload from an `AgentSessionRecord`
- host-handle binding and state-level redispatch metadata
- mapping `worker_budget` and `worker_launch` into host-native intent

The host main agent still projects that intent onto the current tool surface.
For Codex in particular, the adapter may carry `agent_type`, `model`,
`reasoning_effort`, and `service_tier`, while the current `spawn_agent` schema
may intentionally hide those optional fields. The callable projection then
uses `task_name`, `message`, and `fork_turns`; the child inherits the parent
model. This is Codex-native behavior, not an alternate provider integration.

---

## Current Host Differences

| Capability | OpenCode | Codex | Claude Code | Pi RPC |
|---|---|---|---|---|
| Config files | `opencode.json`, `.opencode/` | `.codex/config.example.toml` plus ignored local `.codex/config.toml`, `.codex/skills/goal-plus/`, `.codex/skills/search/`, `.codex/agents/` | `.mcp.json`, `.claude/skills/goal-plus/`, `.claude/skills/search/`, `.claude/agents/` | `.pi/prompts/`, `.pi/skills/goal-plus/`, `.pi/extensions/goal-plus.ts`, Pi console script facades |
| Default worker agent type | `SearchCandidateAgent` | `search_candidate_agent` | `search-candidate-agent` | `search-candidate-worker` prompt asset |
| Launch tool | `Task` | `spawn_agent` | `Agent` | `pi_search_run_batch` convenience driver, `pi_search_run_candidate` single-candidate fallback, or debug-only `pi_rpc_run_worker` / `goal-plus-pi-worker` |
| Worker mode | foreground Task | foreground spawned agent | foreground Agent, `background: false` | foreground `pi --mode rpc` process |
| Launch-schema behavior | fixed `Task` fields from the OpenCode asset | project adapter metadata onto the current `spawn_agent` schema; hidden optional metadata is omitted and the parent model is inherited | project onto the foreground `Agent` tool exposed by the current Claude surface | runner maps payload fields to `pi --mode rpc` CLI/RPC options |
| Worker/orchestrator boundary | `SearchCandidateAgent*` prompt owns one candidate | boundary is present both in `search_candidate_agent.toml` and every launch message, so it survives a hidden `agent_type` field | local agent definition owns one candidate | worker prompt plus runner-owned RPC process owns one candidate |
| Bind tool | `search_bind_opencode_session` | `search_bind_agent_handle` | `search_bind_agent_handle` | `search_bind_agent_handle` |
| Bound handle | OpenCode `metadata.sessionId` | task name, nickname, or returned agent id when available | reusable agent id/name when available; nickname otherwise | Pi `--session-id`, event log paths, assistant text, `metadata.pi_metrics`, or synthetic runner-failure metadata |
| Same-worker continuation | supported with `Task(task_id=...)` | not supported by this adapter | conditional; Agent results may expose an id, but `SendMessage` is not reliable on every `claude -p` tool surface | not supported; use `search_redispatch_candidate` for state-level redispatch |
| Host-native debug evidence | OpenCode DB/log plus `.gp` state | `codex exec --json`, `$CODEX_HOME/sessions` rollouts, optional TUI log | `claude -p --output-format stream-json`, `--debug-file`, `~/.claude/projects` transcripts | metadata-only `.gp/host-logs/pi-rpc-*.jsonl`, optional raw `.txt`, Goal Plus stats custom entry |
| Trace export | supported for OpenCode logs | not implemented | not implemented | not implemented |
| Goal Plus gate enforcement | manual skill/orchestrator calls; no Stop/PreToolUse hook shipped | Codex 0.144.1+ UserPromptSubmit precreation, SessionStart restore, PreToolUse gate, PostToolUse fallback binding, Stop and SubagentStop gates, terminal stats | PostToolUse session binding, session-scoped Stop hook; PreToolUse/SubagentStop manual | pre-model `/goal-plus` creation through native command or print/JSON input transform, persistent interactive custom state, pre-tool gate, turn-level stop gate, stats custom entry; no host process Stop or SubagentStop hook |
| Strategy coverage | baseline host; all existing OpenCode-tested strategies | portable builtin strategies only | portable builtin strategies only | portable builtin strategies only |

## Verification Status

Support levels below distinguish checked-in contracts from real host execution.
They are not claims that every strategy has parity.

| Host/path | Evidence in this repository | Current conclusion |
|---|---|---|
| Codex adapter and assets | `pytest -m codex -q`; launch payload, watchdog, hooks, schema projection, worker boundary, and report assertions | fast contract coverage is in place |
| Codex state-level redispatch | opt-in `codex_redispatch` ST through ordinary `codex exec -m gpt-5.6-terra` | same candidate can resume through a fresh `agent_session_id` and runtime history |
| Codex-native 2 x 2 cycle | opt-in `codex_circle_packing_cycle` ST: two plans of two candidates, four unique worker sessions, four evaluated candidates, final selection/report | real `random` multi-round Search Mode is verified on Codex 0.144.1 with `gpt-5.6-terra` |
| Claude worker budget | manual foreground-subagent `maxTurns` smoke in [worker-budget-smoke.md](worker-budget-smoke.md) | budget mapping is verified; a real two-round Search Mode ST is still pending |
| Pi RPC worker and cycle | Pi-marked unit/integration tests plus opt-in `st_pi_rpc` scenarios | process watchdog, usage metadata, state redispatch, and the 2 x 2 scenario are covered by dedicated paths; rerun the opt-in ST for environment-specific provider evidence |
| OpenCode | compatibility-baseline unit/assets and existing `st_opencode` scenarios | broadest strategy coverage and only implemented trace export |

The Codex cycle exposed two boundaries that unit mocks alone did not prove:

1. `spawn_agent` is dynamically shaped. Passing adapter metadata as mandatory
   arguments fails when `multi_agent_v2.hide_spawn_agent_metadata=true`.
2. A candidate worker must never call parent-owned planning, selection,
   reporting, promotion, or Goal Plus tools. The boundary is embedded in the
   launch message because a hidden `agent_type` also hides the specialized
   worker definition.

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

Single-worker autoresearch is supported by all four host assets: the worker
receives an `agent_session_id`, calls `search_get_agent_context`, works inside
the assigned candidate workspace, runs `search_run_verifier`, and returns a
summary for the main agent to select from.

All worker assets treat `VerifierWorkspaceSideEffect`,
`metrics.infrastructure_failure=true`, and
`metrics.candidate_action=stop_and_report` as terminal for that worker. The
worker must not clean generated verifier files or retry. The parent repairs and
refreezes; under concurrent execution, the host remains responsible for
closing out siblings because the MCP runtime does not supervise processes.

Runtime length control is not currently equivalent across hosts:

| Host | Single-worker autoresearch | Runtime cap exposed by current assets | What the cap controls |
|---|---|---|---|
| OpenCode | supported with the full `SearchCandidateAgent` loop | yes, `steps` in `.opencode/agents/*.md` | host step budget per Task; current tiers are 15, 50, 100, and 150 steps |
| Codex | supported with the project Codex worker prompt | yes, through `worker_budget.max_runtime_seconds` and a parent watchdog | parent waits for the initial interval, sends one closeout message, waits for the final interval, then interrupts on a second timeout |
| Claude Code | supported with the project Claude worker prompt | yes, through `worker_budget.max_turns` and bounded `.claude/agents/*.md` definitions | host turn budget per foreground Agent; current tiers are 4, 8, and 16 turns |
| Pi RPC | supported with `.pi/prompts/search-candidate-worker.md` | yes, through required `worker_budget.max_runtime_seconds` | the runner sends one closeout steer before the deadline, then aborts and kills the Pi RPC process group if it does not exit; `max_turns` is only a prompt hint |

`budget.max_candidates`, `budget.max_parallel`, and strategy round settings
control how many workers the runtime plans and how many candidates it puts in a
planned batch. They do not bound how long an individual host worker thinks or
edits once launched.

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

OpenCode continues to use worker agent tiers such as `SearchCandidateAgentFlash` and
`SearchCandidateAgentDeep`. Codex maps wall-clock budgets to a two-stage watchdog:
wait, send one bounded closeout message, wait again, then interrupt the child.
`spawn_agent` itself does not accept a timeout argument. Claude Code maps turn budgets to `maxTurns`
in the selected local agent definition. When `worker_agent_type` is omitted,
Claude Code budgets of 4, 8, and 16 turns map to `search-candidate-agent-flash`,
`search-candidate-agent`, and `search-candidate-agent-deep` respectively.

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
- Pi RPC `worker_budget` requires `max_runtime_seconds`.
- Pi `pi_search_run_candidate` accepts a one-dispatch `worker_budget` for an
  initial launch or state-level redispatch, while `pi_search_run_batch` accepts
  per-candidate `worker_budgets`. These overrides do not mutate the frozen
  spec. `runtime_multiplier` in `(1, 2]` remains a redispatch-only compatibility
  shortcut.
- Known Claude Code agent types must match their configured `maxTurns`; custom
  Claude agent types are allowed when specified explicitly.

`strategy.worker_launch` is the shared adapter input for host-native launch
choices. Codex includes `model`, `reasoning_effort`, and `service_tier` in its
adapter payload, but the main agent must project that payload onto the current
`spawn_agent` schema. Codex configurations may intentionally hide those
optional fields; an omitted model override inherits the parent model. Pi maps
`model` to its model pattern and `reasoning_effort` to its thinking level.
Service tier is Codex-only. The capability matrix exposes these differences
explicitly rather than pretending every host implements the same primitive.

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

Search history lives in the MCP runtime's `.gp/runs/...` candidate records,
not in a `plan.md` file.

## Strategy Support Matrix

| Strategy or driver | OpenCode | Codex | Claude Code | Pi RPC | Notes |
|---|---|---|---|---|---|
| `agent_guided`, `agent`, `default` | supported | supported | supported | supported | proposal-based; main agent must pass proposals to `search_start_batch` |
| `random`, `random_mode`, `random-mode` | supported | supported | supported | supported | fixed work orders; `search_start_batch` needs no proposals |
| `independent_branches` | supported | not supported | not supported | not supported | treated as OpenCode-only for now, even though it is builtin |
| `evolve` | supported | not supported | not supported | not supported | OpenCode-tested strategy behavior only |
| `openevolve` | supported | not supported | not supported | not supported | OpenCode-tested strategy behavior only |
| `mcts` | supported | not supported | not supported | not supported | OpenCode-tested strategy behavior only |
| Python strategy driver, including `adaptevolve` | supported | not supported | not supported | not supported | non-OpenCode hosts reject non-builtin drivers |
| `external_mcp` strategy driver | OpenCode-only boundary | not supported | not supported | not supported | requires explicit host adaptation before use outside OpenCode |

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
| `adaptevolve` | needs design work | needs design work | uses OpenCode worker tiers such as `SearchCandidateAgentFlash`, `SearchCandidateAgentDeep`, and `SearchCandidateAgentExtraDeep` | Introduce host-neutral tiers like `fast`, `default`, `deep`, `extra_deep`, then map them per adapter |
| `external_mcp` driver | possible, but undefined | possible, but undefined | external planner ownership and MCP availability are not defined across hosts | Define who calls the external planner and how proposals are returned before enabling |
| same-worker continuation algorithms | limited | limited | Codex adapter has no same-worker continuation; Claude Code may expose an agent id but `SendMessage` is not reliable on every tool surface | Prefer state-level resume with new-worker redispatch; treat same-worker continuation as a host-specific optimization only after a real smoke test |
| trace-driven algorithms | not currently | not currently | trace export is only implemented for OpenCode logs | Add host trace exporters or keep these OpenCode-only |

In practice, the safe expansion order is now:

1. Enable `independent_branches`.
2. Enable `evolve`, `openevolve`, and the current `mcts` planner with mock/unit
   coverage first.
3. Run one real two-round non-`random` smoke for Codex; the `random` 2 x 2
   cycle is already verified.
4. Run a real two-round smoke for Claude Code.
5. Redesign worker tiers before enabling `adaptevolve`.
6. Define the external planner contract before enabling `external_mcp`.

---

## Adapter Responsibilities

Adapters live in `src/goal_plus/agent_hosts.py`.

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
  "subagent_type": "SearchCandidateAgent",
  "description": "c001 try alternate parser",
  "prompt": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

Codex launch payload:

```json
{
  "tool": "spawn_agent",
  "task_name": "search_agent_001",
  "agent_type": "search_candidate_agent",
  "fork_turns": "none",
  "message": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

Claude Code launch payload:

```json
{
  "tool": "Agent",
  "agent_type": "search-candidate-agent",
  "description": "c001 try alternate parser",
  "background": false,
  "message": "agent_session_id=agent_001; candidate_id=c001; idea: ..."
}
```

Pi RPC launch payload:

```json
{
  "tool": "pi_rpc_worker",
  "root": "/abs/project/.gp",
  "cwd": "/abs/project/.gp/workspaces/run_001/c001",
  "agent_session_id": "agent_001",
  "candidate_id": "c001",
  "prompt": "agent_session_id=agent_001; candidate_id=c001; idea: ...",
  "session_id": "agent_001",
  "budget_control": {
    "mode": "pi_rpc_process_watchdog",
    "max_runtime_seconds": 600,
    "soft_closeout_seconds": 45,
    "on_exceed": "interrupt"
  }
}
```

The main agent should treat the returned `launch` object as authoritative.
Do not reconstruct it from local assumptions.

## Binding Handles

Binding records host-native identity after the foreground worker starts or
returns:

- OpenCode callers may keep using `search_bind_opencode_session`.
- Codex, Claude Code, and Pi RPC callers use `search_bind_agent_handle`.

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

For Pi RPC, `external_id` is the invocation's Pi `--session-id` and metadata
carries the metadata-only event log path, optional raw text log path, assistant
text, bounded `progress_handoff`, and `pi_metrics` usage/timing summary returned by
`goal-plus-pi-worker`. Workers use `--no-session`; this id is
correlation provenance, not a resumable Pi transcript handle. If the runner
fails before a normal handle is returned, the driver binds a synthetic failure
handle with `runner_failed`, failure stage/type, a bounded error summary, and a
best-effort workspace progress handoff.

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
