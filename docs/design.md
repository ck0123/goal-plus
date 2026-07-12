# Design

## Objective

This project provides `/goal-plus`: a generic goal entrypoint that can upgrade
measurable coding tasks into one or more Search MCP tasks. The runtime owns durable state,
candidate workspaces, budgets, verifier execution, scoring history,
best-candidate selection, reports, and promotion artifacts. The host code-agent
client owns the subagent process lifecycle. The main agent owns policy decisions
through MCP tool calls.

`goal_plus_gate` records deterministic phase decisions, but those decisions are
enforced only when a host actually calls the gate. Codex 0.144.1+ wires
`UserPromptSubmit`, `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, and
`SubagentStop` through `agentic-any-search-mcp --goal-plus-host-hook`. Claude
Code ships PostToolUse ownership binding plus a session-scoped Stop backstop.
OpenCode has no shipped hook; Claude PreToolUse/SubagentStop checkpoints remain
instruction-driven.

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
OpenCode / Codex / Claude Code / Pi host agent
  |
  | reads host-local goal-plus skill
  | calls goal_plus_* MCP tools manually
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
  | appends a search task whenever a verifier-backed spec is ready
  v
SearchTools facade
  |
  | validates API payloads
  v
FileSearchRuntime
  |
  | writes durable state
  v
.gp/
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

`SearchSpec` describes one search job: objective, metric, source path, edit
surface, verifier commands, promotion verifiers, budget, workspace backend,
root hypotheses, and strategy. `workspace.backend` is `copy` by default and
may be set to `git_worktree` for a shared-object Git layout.

`GoalPlusRecord` describes the complete user task. Its canonical
`search_tasks` list is append-only and may contain multiple Search Mode runs.
Each item is identified by `run_id`, references one `frozen_spec_id`, and
stores the recorded selection/report/promotion result. `linked_search` is kept
as a backward-compatible view of the current or most recently linked task.
Legacy records that only contain `linked_search` remain readable, and older
multi-run histories are reconstructed from `search_linked` and
`search_result_recorded` events.

A `search task` is one complete run over one frozen spec. A `search round` is
one persisted `SearchPlan` within that run. Monitoring distinguishes all
planning rounds from rounds whose plan reached `status="started"`.

`StrategySpec` controls planning and execution:

- `name`: strategy mode, default `agent_guided`; alternatives `independent_branches`, `evolve`, `openevolve`, `mcts`, `random`, or Python plugins such as `adaptevolve`
- `driver`: `builtin`, `python`, or `external_mcp`
- `worker_mode`: must be `agent-session-pool` (the only supported value)
- `worker_host`: `opencode`, `codex`, `claude-code`, or `pi-rpc`; default `opencode`
- `worker_agent_type`: optional default host hint such as OpenCode `AnySearchAgent`; a strategy plan may override it through `worker_policy`
- `worker_budget`: optional per-worker runtime budget. Codex requires
  `max_runtime_seconds` and enforces it through a parent watchdog. Claude Code
  requires `max_turns` and enforces it through the selected agent definition.
  Pi RPC requires `max_runtime_seconds`, sends a closeout steer before the
  deadline, and retains a hard process watchdog.
- `history_policy`, `parent_policy`, and `config`: strategy-specific controls

Retired `worker_mode` values (`main-agent-search-direct`, `auto`, `sub-agent-search-dispatch`) and string-form `strategy` are rejected at parse time. Fix the spec instead of relying on normalization.

`SearchPlan` is produced by `search_plan_next`. It contains worker policy, requested/planned batch size, official history, derivation policy, optional proposal contract, fixed work orders, and strategy trace.

`CandidateTask` is produced by `search_start_batch`. It contains the candidate
workspace path, workspace backend, optional branch/base revision, allowed and
denied files, candidate lineage, plan metadata, and local instructions.

`AgentSessionRecord` is produced by `search_start_agent_session` or `search_redispatch_candidate`. It is a **context/provenance handle**, not a lifecycle record. It carries the agent_session_id, run_id, candidate_id, host, host_handle, optional legacy opencode_session_id, workspace, directive, host-native launch payload, and counters (verifier_runs). There is no status, phase, heartbeat, or terminal state on this record — those belong to the host client.

`IterationRecord` is produced by every `search_run_verifier` call. It records
the iteration number, agent_session_id (or None for main final verify), score,
failure_class, changed files, metrics, and the candidate workspace's real
`git_head` when available. Before running the verifier, the runtime
automatically commits changed candidate artifact files in the candidate
workspace so it can later checkout the best committed iteration.
There is no separate submit step.

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
goal_plus_link_search_run  (append this run as one search task)
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
search_redispatch_candidate  (optional state-level resume with fresh session)
  |
  v
Host foreground worker runs the same candidate workspace with optional tier/budget override
  |
  v
search_plan_next  (optional follow-up batch)
  |
  v
search_select  (ranks verifier iterations, checks out selected git_head, final-verifies)
  |
  v
search_report
  |
  v
search_promote
```

After `goal_plus_record_search_result` and the raw-goal audit, the main agent
may finish the Goal Plus task or create and link another search task. Starting
another task does not overwrite earlier task evidence.

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

`budget.max_parallel` caps how many candidates `search_plan_next` places in one
planned batch. The runtime does not supervise host worker lifecycle after the
host launches those workers.

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
2. Project the launch payload onto the current host tool schema and spawn host
   workers as foreground calls. This matters for Codex configurations that hide
   optional `spawn_agent` metadata.
3. Wait for the host worker to return. There is no MCP wait loop.
4. Verify completed candidates with `search_run_verifier(run_id, candidate_id, "process")` (without `agent_session_id`) to confirm the final score.
5. Start more sessions when candidate budget remains.
6. Select, report, and promote only through runtime APIs.

The MCP runtime does not perform process supervision. Stopping a running subagent is a host/user concern, not an MCP call.

## Verification And Isolation

Verifier commands run from each candidate workspace. The runtime adds the workspace to `PYTHONPATH` and parses the last JSON object printed to stdout as metrics. Hard gates such as edit-surface violations and frozen verifier hash failures force the score to `0.0`.

The default `copy` backend creates an independent source snapshot for each
candidate:

```text
.gp/runs/<run_id>/workspace/c001/
.gp/runs/<run_id>/workspace/c002/
```

The `git_worktree` backend snapshots `source_path` once into a normal run-local
repository, then creates each candidate with `git worktree`:

```text
.gp/runs/<run_id>/workspace-repository/       # shared objects + baseline branch
.gp/runs/<run_id>/workspace/c001/             # gp/<run_id>/c001
.gp/runs/<run_id>/workspace/c002/             # gp/<run_id>/c002
```

The runtime accepts either Git or non-Git input because the run-local baseline
is always its own snapshot. A first-generation candidate starts from
`gp/<run_id>/baseline`. A follow-up candidate starts from the chosen parent's
best verifier-recorded commit, preserving explicit branch lineage and avoiding
uncommitted parent state. Worktrees share Git object storage, but each still
materializes its checked-out files; this reduces repository-history duplication
rather than eliminating all per-candidate disk use.

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
- `.codex/skills/search/SKILL.md`, `.codex/agents/any_search_agent.toml`: Codex host assets
- `.claude/skills/search/SKILL.md`, `.claude/agents/any-search-agent.md`: Claude Code host assets
- `.pi/skills/goal-plus/SKILL.md`, `.pi/prompts/any-search-worker.md`, `.pi/extensions/search-runtime.ts`: Pi host assets; Pi folds Search Mode guidance into the single user-facing `goal-plus` skill

## Current Boundary

The runtime owns specs, plans, workspaces, verifier execution, scoring history, reports, and promotion. The selected host client owns subagent lifecycle (start, run, enforce local budgets, stop/interrupt, return/inject completion). The runtime records `verifier_runs` counters and iteration provenance per `agent_session_id` for audit; it does not model session status, phase, terminal state, or process cancellation. There is no MCP wait loop, no MCP abort, and no MCP finalize.

## Information Flow Reference

This doc covers the data model and state machine. For **which agent does which step, what each agent actually sees, and which OpenCode platform constraints gate the flow**, see [flow-view.md](flow-view.md). Consult it before designing strategy changes (evolve, mcts, hybrid) to avoid building on APIs the platform does not actually expose.
