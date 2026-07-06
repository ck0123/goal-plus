# Design

## Objective

This project provides `/goal-plus`: a generic goal entrypoint that can upgrade
measurable coding tasks into Search MCP runs. The runtime owns durable state,
candidate workspaces, budgets, verifier execution, scoring history,
best-candidate selection, reports, and promotion artifacts. The host code-agent
client owns the subagent process lifecycle. The main agent owns policy decisions
through MCP tool calls.

The current design is **not** a supervisor loop. The runtime is a scoring and artifact runtime; it does not supervise subagent lifecycle state:

- freeze a `SearchSpec` and verifier artifacts
- create isolated candidate workspaces
- plan the next candidate batch
- create a context handle (AgentSessionRecord) and return a host-native launch payload
- the main agent uses the launch payload to spawn a foreground worker in the selected host
- the subagent self-scores via verifier calls
- the host worker returns to the main agent
- the main agent final-confirms the score, then selects, reports, and optionally promotes

The MCP runtime does not wait, abort, finalize, submit, observe, or host-sync subagent state. Those responsibilities belong to the host client (lifecycle) or to the main agent (selection).

## Architecture

```text
User
  |
  | "/goal-plus: improve this measurable task"
  v
OpenCode / Codex / Claude Code host agent
  |
  | reads host-local goal-plus skill
  | calls goal_plus_* MCP tools
  v
Search MCP server
  |
  | delegates JSON tool calls
  v
GoalPlusTools facade
  |
  | records goal state, triage, confirmation, gates
  v
FileGoalPlusRuntime
  |
  | links to Search Mode when verifier-backed spec is ready
  v
SearchTools facade
  |
  | validates API payloads
  v
FileSearchRuntime
  |
  | writes durable state
  v
.search/
  goal-plus/<goal_plus_id>/
    goal.json
    events.jsonl
  specs/<frozen_spec_id>/
  runs/<run_id>/
    run.json
    plans/<plan_id>.json
    candidates/<candidate_id>/candidate.json
    candidates/<candidate_id>/task.json
    workspace/<candidate_id>/
    agent_sessions/<agent_session_id>.json
    report.md
    promotion/<candidate_id>.patch
```

## Core Data Model

`SearchSpec` describes one search job: objective, metric, source path, edit surface, verifier commands, promotion verifiers, budget, root hypotheses, and strategy.

`StrategySpec` controls planning and execution:

- `name`: strategy mode, default `agent_guided`; alternatives `independent_branches`, `evolve`, `openevolve`, `mcts`, `random`, or Python plugins such as `adaptevolve`
- `driver`: `builtin`, `python`, or `external_mcp`
- `worker_mode`: must be `agent-session-pool` (the only supported value)
- `worker_host`: `opencode`, `codex`, or `claude-code`; default `opencode`
- `worker_agent_type`: optional default host hint such as OpenCode `AnySearchAgent`; a strategy plan may override it through `worker_policy`
- `worker_budget`: optional per-worker runtime budget. Codex requires
  `max_runtime_seconds` and enforces it through a parent watchdog. Claude Code
  requires `max_turns` and enforces it through the selected agent definition.
- `history_policy`, `parent_policy`, and `config`: strategy-specific controls

Retired `worker_mode` values (`main-agent-search-direct`, `auto`, `sub-agent-search-dispatch`) and string-form `strategy` are rejected at parse time. Fix the spec instead of relying on normalization.

`SearchPlan` is produced by `search_plan_next`. It contains worker policy, requested/planned batch size, official history, derivation policy, optional proposal contract, fixed work orders, and strategy trace.

`CandidateTask` is produced by `search_start_batch`. It contains the candidate workspace path, allowed/denied files, candidate lineage, plan metadata, and local instructions.

`AgentSessionRecord` is produced by `search_start_agent_session`. It is a **context/provenance handle**, not a lifecycle record. It carries the agent_session_id, run_id, candidate_id, host, host_handle, optional legacy opencode_session_id, workspace, directive, host-native launch payload, and counters (verifier_runs). There is no status, phase, heartbeat, or terminal state on this record — those belong to the host client.

`IterationRecord` is produced by every `search_run_verifier` call. It records the iteration number, agent_session_id (or None for main final verify), score, failure_class, changed files, and metrics. There is no separate submit step.

`ScoreReport` is produced by `search_run_verifier`. It records pass/fail state, aggregate score, raw metrics, changed-file violations, frozen verifier violations, and failure class.

## State Flow

```text
draft SearchSpec
  |
  v
search_freeze_spec
  |
  v
search_create
  |
  v
search_plan_next
  |
  v
search_start_batch
  |
  v
search_start_agent_session  (returns launch payload)
  |
  v
Host foreground worker runs using launch payload
  |
  v
search_bind_opencode_session or search_bind_agent_handle  (records host handle)
  |
  v
subagent calls search_get_agent_context
  |
  v
subagent calls search_run_verifier during its iteration loop
  |
  v
Host worker returns to Main
  |
  v
Main calls search_run_verifier (final confirm, no agent_session_id)
  |
  v
search_continue_agent_session  (optional, host capability dependent)
  |
  v
Host worker continues if the adapter supports same-worker continuation
  |
  v
search_plan_next  (optional follow-up batch)
  |
  v
search_select
  |
  v
search_report
  |
  v
search_promote
```

There is no batch-shortcut compatibility helper. For fixed-work-order strategies, call `search_plan_next` followed by `search_start_batch`. For proposal-based strategies, do the same and pass proposals to `start_batch`.

## Agent Host Adapters

Host-specific worker lifecycle details live behind `agent_hosts.py`. The
runtime continues to own specs, plans, workspaces, verifier execution, reports,
and promotion. Adapters only describe how a main agent should launch, bind, and
optionally continue a worker in a specific code-agent client.

OpenCode is the default compatibility baseline. Codex and Claude Code use the
same runtime state machine, but narrower host-native launch and continuation
semantics. See [agent-host-adapters.md](agent-host-adapters.md) for the current
OpenCode/Codex/Claude Code capability matrix and adapter contract.

## Budget Model

`budget.max_candidates` limits total candidate workspaces and is enforced by planning/start APIs.

`budget.max_parallel` is a batch planning hint. The runtime does not gate session creation on it and does not supervise Task lifecycle.

There are no runtime-owned time-based deadlines. Host workers run until their
host-local budget, step cap, or user interruption stops them. OpenCode worker
tiers use `AnySearchAgent` (default, 50 steps), `AnySearchAgentFlash` (15),
`AnySearchAgentDeep` (100), or `AnySearchAgentExtraDeep` (150). Codex and
Claude Code use their own foreground agent limits. Users can interrupt anytime
and query current best via `search_list_history` / `search_status`. There is no
MCP abort tool; stopping a running subagent is a host/user concern.

## Main Agent Responsibilities

In `agent-session-pool` mode the main agent should:

1. Call `search_start_agent_session` for each candidate it wants to dispatch.
2. Use the launch payload verbatim to spawn host workers as foreground calls.
3. Wait for the host worker to return. There is no MCP wait loop.
4. Verify completed candidates with `search_run_verifier(run_id, candidate_id, "process")` (without `agent_session_id`) to confirm the final score.
5. Start more sessions when candidate budget remains.
6. Select, report, and promote only through runtime APIs.

The MCP runtime does not perform process supervision. Stopping a running subagent is a host/user concern, not an MCP call.

## Verification And Isolation

Verifier commands run from each candidate workspace. The runtime adds the workspace to `PYTHONPATH` and parses the last JSON object printed to stdout as metrics. Hard gates such as edit-surface violations and frozen verifier hash failures force the score to `0.0`.

Candidate workspaces are copied from `source_path`:

```text
.search/runs/<run_id>/workspace/c001/
.search/runs/<run_id>/workspace/c002/
```

Each candidate workspace contains `.tmp/` for notes and non-scoring static drafts. Runtime tree hashing ignores `.tmp/`.

Workers must not modify denied files, frozen verifier artifacts, or the main source workspace. Workers may run static checks such as `py_compile`; actual scoring belongs to the runtime verifier.

## Implemented Modules

- `models.py`: strict Pydantic models for specs, candidates, iterations, score reports, run records, host handles, and the simplified AgentSessionRecord context handle
- `agent_hosts.py`: OpenCode, Codex, and Claude Code launch/bind/continue capability adapters
- `runtime.py`: file-backed state machine, workspace copy, adapter-backed launch payload generation, verifier execution, selection, report, patch export
- `tools.py`: JSON-friendly facade used by tests and MCP
- `server.py`: FastMCP stdio server for host clients
- `.opencode/skills/search/SKILL.md`: host-agent workflow guide
- `.opencode/agents/AnySearchAgent*.md`: managed subagent prompts
- `.agents/skills/search/SKILL.md`, `.codex/agents/any_search_agent.toml`: Codex host assets
- `.claude/skills/search/SKILL.md`, `.claude/agents/any-search-agent.md`: Claude Code host assets

## Current Boundary

The runtime owns specs, plans, workspaces, verifier execution, scoring history, reports, and promotion. The selected host client owns subagent lifecycle (start, run, enforce local budgets, stop/interrupt, return/inject completion). The runtime records `verifier_runs` counters and iteration provenance per `agent_session_id` for audit; it does not model session status, phase, terminal state, or process cancellation. There is no MCP wait loop, no MCP abort, and no MCP finalize.

## Information Flow Reference

This doc covers the data model and state machine. For **which agent does which step, what each agent actually sees, and which OpenCode platform constraints gate the flow**, see [flow-view.md](flow-view.md). Consult it before designing strategy changes (evolve, mcts, hybrid) to avoid building on APIs the platform does not actually expose.
