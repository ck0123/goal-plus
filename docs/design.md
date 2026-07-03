# Design

## Objective

This project provides a generic Search MCP Runtime for measurable coding tasks. The runtime owns durable state, candidate workspaces, budgets, verifier execution, scoring history, best-candidate selection, reports, and promotion artifacts. OpenCode owns the subagent process lifecycle. The main agent owns policy decisions through MCP tool calls.

The current design is **not** a supervisor loop. The runtime is a scoring and artifact runtime; it does not supervise subagent lifecycle state:

- freeze a `SearchSpec` and verifier artifacts
- create isolated candidate workspaces
- plan the next candidate batch
- create a context handle (AgentSessionRecord) and return the OpenCode Task launch payload
- the main agent uses the launch payload to spawn an OpenCode Task
- the subagent self-scores via verifier calls
- OpenCode Task returns to the main agent
- the main agent final-confirms the score, then selects, reports, and optionally promotes

The MCP runtime does not wait, abort, finalize, submit, observe, or host-sync subagent state. Those responsibilities belong to OpenCode (lifecycle) or to the main agent (selection).

## Architecture

```text
User
  |
  | "load examples/k_module_search_spec.json and run the search"
  v
OpenCode host agent
  |
  | reads .opencode/skills/search/SKILL.md
  | calls MCP tools
  v
Search MCP server
  |
  | delegates JSON tool calls
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

- `name`: strategy mode, default `agent_guided`; alternatives `independent_branches`, `evolve`, `mcts`, `random`, or Python plugins such as `adaptevolve`
- `driver`: `builtin`, `python`, or `external_mcp`
- `worker_mode`: must be `agent-session-pool` (the only supported value)
- `worker_agent_type`: optional default host hint such as OpenCode `AnySearchAgent`; a strategy plan may override it through `worker_policy`
- `history_policy`, `parent_policy`, and `config`: strategy-specific controls

Retired `worker_mode` values (`main-agent-search-direct`, `auto`, `sub-agent-search-dispatch`) and string-form `strategy` are rejected at parse time. Fix the spec instead of relying on normalization.

`SearchPlan` is produced by `search_plan_next`. It contains worker policy, requested/planned batch size, official history, derivation policy, optional proposal contract, fixed work orders, and strategy trace.

`CandidateTask` is produced by `search_start_batch`. It contains the candidate workspace path, allowed/denied files, candidate lineage, plan metadata, and local instructions.

`AgentSessionRecord` is produced by `search_start_agent_session`. It is a **context/provenance handle**, not a lifecycle record. It carries the agent_session_id, run_id, candidate_id, optional opencode_session_id, workspace, directive, launch payload (subagent_type/description/prompt, plus task_id for continuation), and counters (verifier_runs). There is no status, phase, heartbeat, or terminal state on this record — those belong to OpenCode.

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
OpenCode Task runs subagent using launch payload
  |
  v
search_bind_opencode_session  (records Task metadata.sessionId)
  |
  v
subagent calls search_get_agent_context
  |
  v
subagent calls search_run_verifier during its iteration loop
  |
  v
OpenCode Task returns to Main
  |
  v
Main calls search_run_verifier (final confirm, no agent_session_id)
  |
  v
search_continue_agent_session  (optional same OpenCode session/node)
  |
  v
OpenCode Task runs with task_id=launch.task_id
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

## Budget Model

`budget.max_candidates` limits total candidate workspaces and is enforced by planning/start APIs.

`budget.max_parallel` is a batch planning hint. The runtime does not gate session creation on it and does not supervise Task lifecycle.

There are no time-based deadlines. Subagents run until their OpenCode step cap (15/50/100/150, set by `strategy.worker_agent_type` or a plan-level `worker_policy` override) hits or until the user interrupts the run. Users can interrupt anytime and query current best via `search_list_history` / `search_status`. There is no MCP abort tool — stopping a running subagent is an OpenCode/user interruption concern.

## Main Agent Responsibilities

In `agent-session-pool` mode the main agent should:

1. Call `search_start_agent_session` for each candidate it wants to dispatch.
2. Use the launch payload verbatim to spawn OpenCode Task workers as foreground Task calls.
3. Wait for OpenCode Task to return. There is no MCP wait loop.
4. Verify completed candidates with `search_run_verifier(run_id, candidate_id, "process")` (without `agent_session_id`) to confirm the final score.
5. Start more sessions when candidate budget remains.
6. Select, report, and promote only through runtime APIs.

The MCP runtime does not perform process supervision. Stopping a running subagent is an OpenCode/user concern, not an MCP call.

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

- `models.py`: strict Pydantic models for specs, candidates, iterations, score reports, run records, and the simplified AgentSessionRecord context handle
- `runtime.py`: file-backed state machine, workspace copy, launch payload generation, verifier execution, selection, report, patch export
- `tools.py`: JSON-friendly facade used by tests and MCP
- `server.py`: FastMCP stdio server for OpenCode
- `.opencode/skills/search/SKILL.md`: host-agent workflow guide
- `.opencode/agents/AnySearchAgent*.md`: managed subagent prompts

## Current Boundary

The runtime owns specs, plans, workspaces, verifier execution, scoring history, reports, and promotion. OpenCode owns subagent lifecycle (start, run, enforce step cap, stop/interrupt, return/inject completion). The runtime records `verifier_runs` counters and iteration provenance per `agent_session_id` for audit; it does not model session status, phase, terminal state, or process cancellation. There is no MCP wait loop, no MCP abort, and no MCP finalize.

## Information Flow Reference

This doc covers the data model and state machine. For **which agent does which step, what each agent actually sees, and which OpenCode platform constraints gate the flow**, see [flow-view.md](flow-view.md). Consult it before designing strategy changes (evolve, mcts, hybrid) to avoid building on APIs the platform does not actually expose.
