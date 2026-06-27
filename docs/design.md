# Design

## Objective

This project provides a generic Search MCP Runtime for measurable coding tasks. The runtime owns durable state, candidate workspaces, budgets, verifier execution, agent-session events, best-candidate selection, reports, and promotion artifacts. The host agent owns policy decisions through MCP tool calls.

The current design is centered on a supervisor loop:

- freeze a `SearchSpec` and verifier artifacts
- create isolated candidate workspaces
- plan the next candidate batch
- start managed agent sessions up to `budget.max_parallel`
- wait for session events or deadlines
- abort/finalize stuck sessions
- verify submitted candidates through runtime-owned checks
- select, report, and optionally promote

The retired two-channel worker-dispatch API is not part of the public MCP surface.

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
    agent_events/<event_id>.json
    observations/<observation_id>.json
    report.md
    promotion/<candidate_id>.patch
```

## Core Data Model

`SearchSpec` describes one search job: objective, metric, source path, edit surface, verifier commands, promotion verifiers, budget, root hypotheses, and strategy.

`StrategySpec` controls planning and execution:

- `name`: strategy mode, for example `independent_branches`, `agent_guided`, `evolve`, or `mcts`
- `driver`: `builtin`, `python`, or `external_mcp`
- `worker_mode`: must be `agent-session-pool` (the only supported value)
- `worker_agent_type`: optional host hint such as OpenCode `AnySearchAgent`
- `worker_timeout_seconds`: default per-session wall-clock budget
- `worker_local_verifier_max_runs`: local scoring/evaluator budget for the subagent, defaulting to 3 (minimum 1; 0 is forbidden)
- `history_policy`, `parent_policy`, and `config`: strategy-specific controls

Retired `worker_mode` values (`main-agent-search-direct`, `auto`, `sub-agent-search-dispatch`) are normalized to `agent-session-pool` at parse time so legacy specs keep working.

`SearchPlan` is produced by `search_plan_next`. It contains worker policy, requested/planned batch size, official history, derivation policy, optional proposal contract, fixed work orders, and strategy trace.

`CandidateTask` is produced by `search_start_batch` or `search_next_batch`. It contains the candidate workspace path, allowed/denied files, candidate lineage, plan metadata, and local instructions.

`AgentSessionRecord` is produced by `search_start_agent_session`. It records a durable subagent session: candidate id, directive, workspace, status/phase, heartbeat, budget, counters, summary, and result.

`AgentSessionEvent` records session lifecycle and supervisor wakeups: started, status updated, blocked, finalize requested, completed, failed, aborted, timed out, observation published, and run deadline.

`ArtifactBundle` is submitted after a candidate workspace is ready. In `agent-session-pool` mode it must include the producing `agent_session_id`. Runtime verifier results, not artifact summaries, determine scores.

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
search_start_agent_session  (for agent-session-pool)
  |
  v
background/managed subagent calls search_get_agent_context
  |
  v
search_wait_agent_events supervisor loop
  |
  v
search_submit_candidate
  |
  v
search_finish_agent_session
  |
  v
search_run_verifier
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

`search_next_batch(run_id, k)` remains as a compatibility helper for fixed-work-order strategies. For proposal-based strategies, use `search_plan_next` followed by `search_start_batch` with proposals.

## Budget Model

`budget.max_candidates` limits total candidate workspaces and is enforced by planning/start APIs.

`budget.max_parallel` limits active agent sessions and is enforced by `search_start_agent_session`.

`budget.wall_clock_seconds` is the run-level deadline. `search_wait_agent_events` wakes on `run_deadline`, and the supervisor should call `search_abort_all_agent_sessions` before reporting submitted candidates.

`strategy.worker_timeout_seconds` or `budget.max_worker_seconds` defines the MCP per-session wall-clock budget. Runtime truncates session deadlines to remaining run time. For OpenCode hosts, this is not a `Task` timeout parameter; the main agent must keep control through `Task(background=true)` and enforce the deadline in the supervisor loop.

`AgentSessionBudget.max_verifier_runs` is an optional counter. Exceeding it moves the session to finalizing so the supervisor/subagent can submit best-so-far work. (The runtime no longer tracks step/tool counters — only `verifier_runs` is auto-incremented inside `run_verifier`.)

## Supervisor Responsibilities

The host agent must not hide long-running work inside foreground subagent calls. In `agent-session-pool` mode it should:

1. Start sessions only while active count is below `max_parallel`.
2. Launch subagents only as background/managed tasks that return control immediately. For OpenCode, start the parent process with `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true` and call Task with `background: true`. If the host cannot do this, do not use `agent-session-pool` for subagents.
3. Poll with `search_wait_agent_events`, passing the previous response's `last_event_id` as the next `since_event_id`.
4. Verify completed candidates.
5. Start more sessions when slots free and candidate budget remains.
6. Request finalization for stale or blocked sessions when useful.
7. Abort individual sessions or all sessions when budgets are exhausted.

The MCP runtime records the authoritative control-plane state. Actual OS/process-level cancellation requires the host adapter to wire runtime abort requests to the host's native child-session abort.

## Verification And Isolation

Verifier commands run from each candidate workspace. The runtime adds the workspace to `PYTHONPATH` and parses the last JSON object printed to stdout as metrics. Hard gates such as edit-surface violations and frozen verifier hash failures force the score to `0.0`.

Candidate workspaces are copied from `source_path`:

```text
.search/runs/<run_id>/workspace/c001/
.search/runs/<run_id>/workspace/c002/
```

Each candidate workspace contains `.tmp/` for notes and non-scoring static drafts. Runtime tree hashing ignores `.tmp/`.

Workers must not modify denied files, frozen verifier artifacts, or the main source workspace. Workers may run static checks such as `py_compile`; actual scoring belongs to the runtime verifier unless the session has a nonzero local verifier budget.

## Implemented Modules

- `models.py`: strict Pydantic models for specs, candidates, artifacts, scores, run records, agent sessions, events, and observations
- `runtime.py`: file-backed state machine, workspace copy, session pool, verifier execution, selection, report, patch export
- `tools.py`: JSON-friendly facade used by tests and MCP
- `server.py`: FastMCP stdio server for OpenCode
- `.opencode/skills/search/SKILL.md`: host-agent workflow guide
- `.opencode/agents/AnySearchAgent.md`: managed subagent prompt

## Current Boundary

The runtime records and enforces MCP-level session state, pool admission, and deadlines. Killing a currently running OpenCode child session still needs host integration: the host must translate `search_abort_agent_session` or `search_abort_all_agent_sessions` into native OpenCode session abort. Until that adapter is wired, abort is authoritative runtime state but not guaranteed process termination.
