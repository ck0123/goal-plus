---
name: search
description: >
  Run MCP-controlled Search Mode for measurable multi-candidate coding tasks.
  Use when the user invokes /search or asks to try several candidate fixes,
  optimizations, or configurations under a frozen verifier.
argument-hint: >
  Objective, source path, allowed files, verifier command/artifacts, budget.
---

# Agentic Search Skill

This skill runs Search Mode with MCP-owned state, isolated candidate workspaces, verifier execution, durable agent sessions, and a supervisor wait/abort loop.

The old worker-dispatch flow is retired and is not part of the MCP tool surface. Long-running subagents must be represented by `search_start_agent_session` and supervised with `search_wait_agent_events`.

## OpenCode Host Requirement

For `agent-session-pool`, OpenCode must expose background subagents. Start OpenCode with one of:

```bash
OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true opencode
OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true opencode run --command search "<prompt>"
```

`OPENCODE_EXPERIMENTAL=true` also enables the same flag. This environment variable belongs to the OpenCode process itself, not only to the MCP server subprocess configured in `.opencode/opencode.json`.

OpenCode `Task` currently has no `timeout` parameter. Search session timeouts are MCP supervisor deadlines: the main agent must launch subagents with `background: true`, wait with `search_wait_agent_events`, and mark/finalize/abort runtime sessions when budgets expire. Do not pass or invent a Task-level timeout.

## Tool Names In OpenCode

The MCP server is configured as `search-runtime`, so tools appear with this prefix:

| Runtime tool | OpenCode tool name |
|---|---|
| `search_freeze_spec` | `search-runtime_search_freeze_spec` |
| `search_create` | `search-runtime_search_create` |
| `search_status` | `search-runtime_search_status` |
| `search_list_history` | `search-runtime_search_list_history` |
| `search_plan_next` | `search-runtime_search_plan_next` |
| `search_start_batch` | `search-runtime_search_start_batch` |
| `search_next_batch` | `search-runtime_search_next_batch` |
| `search_start_agent_session` | `search-runtime_search_start_agent_session` |
| `search_get_agent_context` | `search-runtime_search_get_agent_context` |
| `search_update_agent_status` | `search-runtime_search_update_agent_status` |
| `search_list_agent_status` | `search-runtime_search_list_agent_status` |
| `search_finish_agent_session` | `search-runtime_search_finish_agent_session` |
| `search_request_agent_finalize` | `search-runtime_search_request_agent_finalize` |
| `search_abort_agent_session` | `search-runtime_search_abort_agent_session` |
| `search_abort_all_agent_sessions` | `search-runtime_search_abort_all_agent_sessions` |
| `search_record_agent_step` | `search-runtime_search_record_agent_step` |
| `search_publish_observation` | `search-runtime_search_publish_observation` |
| `search_list_observations` | `search-runtime_search_list_observations` |
| `search_wait_agent_events` | `search-runtime_search_wait_agent_events` |
| `search_submit_candidate` | `search-runtime_search_submit_candidate` |
| `search_run_verifier` | `search-runtime_search_run_verifier` |
| `search_select` | `search-runtime_search_select` |
| `search_report` | `search-runtime_search_report` |
| `search_promote` | `search-runtime_search_promote` |
| `search_abort` | `search-runtime_search_abort` |

If these tools are unavailable, stop and report that the MCP server is not connected. Do not simulate runtime state in chat.

## Required Discipline

1. Do not start candidate execution before freezing the SearchSpec and verifier artifacts.
2. Do not modify verifier files during candidate execution.
3. Do not edit the main source workspace while exploring candidates.
4. Do not accept subagent-reported scores. Always call `search_run_verifier`.
5. Do not promote by manually copying files. Use `search_promote`; it exports a patch/report.
6. If a candidate touches denied files or files outside the edit surface, submit it anyway and let runtime mark it failed.
7. For `agent-session-pool`, do not use foreground long-running Task calls. The main agent must be able to wake, inspect, start more work, request finalize, or abort.

## SearchSpec

Minimum shape:

```json
{
  "objective": "measurable task objective",
  "metric_name": "primary_metric",
  "metric_direction": "maximize",
  "source_path": "path/to/project",
  "edit_surface": {
    "allow": ["files/or/globs/the/candidate/may/edit"],
    "deny": ["verifier/or/config/files"]
  },
  "process_verifiers": [
    {
      "name": "ranking_signal",
      "role": "ranking_signal",
      "command": ["command", "arg"],
      "timeout_seconds": 30
    }
  ],
  "promotion_verifiers": [
    {
      "name": "anti_cheat_gate",
      "role": "anti_cheat_gate",
      "command": ["search-runtime-internal", "check-frozen-hashes"]
    }
  ],
  "budget": {
    "max_candidates": 4,
    "max_parallel": 2,
    "wall_clock_seconds": 300,
    "max_worker_seconds": 180
  },
  "strategy": {
    "name": "independent_branches",
    "driver": "builtin",
    "worker_mode": "agent-session-pool",
    "worker_agent_type": "AnySearchAgent",
    "worker_timeout_seconds": 180,
    "worker_local_verifier_max_runs": 0,
    "history_policy": {
      "scope": "top_n",
      "top_n": 5
    }
  }
}
```

`max_candidates` and `max_parallel` are enforced by the runtime. `wall_clock_seconds` is the run budget. `worker_timeout_seconds` is the default MCP session budget, capped by remaining run time. It is not an OpenCode `Task` parameter. Prefer small worker budgets for exploratory examples; use larger values only when the verifier/task genuinely needs them.

`strategy.worker_mode` values:

- `main-agent-search-direct`: the main agent edits candidate workspaces directly.
- `agent-session-pool`: candidate execution is done by managed subagent sessions. The runtime enforces pool admission and records deadlines/events.
- `auto`: runtime resolves the effective mode.

Use `agent-session-pool` for all managed-subagent specs.

## Workflow

### Step 1: Probe Read-Only Context

Read enough files to identify:

- objective and metric
- source path
- allowed edit files
- denied verifier/config files
- process verifier command
- promotion verifier command, if any
- budget: `max_candidates`, `max_parallel`, `wall_clock_seconds`, worker/session budget

For bundled examples, load the matching JSON file from `examples/`. If the user gives extra budget instructions, modify the spec object before freezing.

Treat "start by requesting N candidates" as `search_plan_next(..., requested_k=N)`. Do not change `budget.max_candidates`, `budget.max_parallel`, or `wall_clock_seconds` unless the user explicitly describes total budget, pool size, or run deadline.

### Step 2: Confirm

Before calling runtime tools, summarize objective, metric, source path, edit surface, frozen verifier artifacts, and budget. Ask only when ambiguous or risky.

### Step 3: Freeze And Create

Call:

```text
search-runtime_search_freeze_spec(spec=<spec>, verifier_artifact_paths=[...])
search-runtime_search_create(frozen_spec_id="<id>")
```

Record `run_id`.

### Step 4: Plan And Start Candidate Workspaces

Call:

```text
search-runtime_search_plan_next(run_id="<run_id>", requested_k=<k>)
search-runtime_search_start_batch(run_id="<run_id>", plan_id="<plan_id>", proposals=<optional>)
```

Use `search_next_batch` only for fixed-work-order strategies when proposals are not required.

Each returned `CandidateTask` owns an isolated workspace. Candidate work must stay inside that workspace and only modify allowed files.

### Step 5: Supervise Agent Sessions

For `worker_policy.mode == "agent-session-pool"`:

1. Start at most `budget.max_parallel` sessions.
2. For each candidate, call `search-runtime_search_start_agent_session` with the candidate id, directive, and a session budget.
3. Launch the subagent with `subagent_type=worker_policy.subagent_type` when present, passing only the `agent_session_id` and a concise candidate idea/directive.
4. In OpenCode, the Task call must include `background: true`. This requires `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true`; if the Task schema does not expose `background`, supervised subagents are unavailable in this host process.
5. Launch workers only through a background/managed mechanism that returns control to the main agent immediately. Do not issue foreground long-running Task calls that block the main agent from waiting or aborting. If no background/managed launch is available, do not run `agent-session-pool`; switch to direct candidate work or stop and report that supervised subagents are unavailable.
6. Enter a supervisor loop with `search-runtime_search_wait_agent_events(run_id, timeout_seconds=<poll window>, since_event_id=<last seen>)`.
7. On session completion/failure, verify any submitted candidate and start another candidate if budget remains and the pool has capacity.
8. On `agent_blocked`, decide whether to wait, adjust the plan, request finalization, or abort that session.
9. On `agent_timed_out` or run deadline, call `search-runtime_search_abort_agent_session` or `search-runtime_search_abort_all_agent_sessions`, then summarize/verify whatever has been submitted.
10. If `search_wait_agent_events` itself times out without events, call `search_list_agent_status` and choose: keep waiting, request finalize, start available work, or abort at budget exhaustion.

Hard host rule:

- A normal OpenCode `Task` call is not a supervised background launch unless the host explicitly provides a background/managed option and returns control before the worker finishes.
- In current OpenCode, that means Task input must include `background: true`. There is no supported `timeout` field on Task; use MCP session deadlines and the supervisor loop instead.
- The Task prompt must not hard-code `run_id`, `candidate_id`, or workspace paths for the worker to use. The worker must derive them from `search_get_agent_context(agent_session_id)`. Human-readable candidate ideas are fine; authoritative identifiers and paths come only from MCP context.
- Seeing `AnySearchAgent Task — ...` followed by worker tool activity, with no immediate `search_wait_agent_events` call from the main agent, means the main agent is blocked in foreground Task execution.
- Do not launch foreground Task calls "to see if they block". If foreground Task is the only available subagent mechanism, do not use `agent-session-pool` subagents for that run.

Supervisor loop sketch:

```text
last_event_id = null
pending_candidates = [...]
while pending_candidates or active_sessions:
  while pending_candidates and active_count < max_parallel:
    session = search_start_agent_session(...)
    launch AnySearchAgent Task(background=true) with session.agent_session_id
    active_count += 1

  wait = search_wait_agent_events(run_id, timeout_seconds=300, since_event_id=last_event_id)
  last_event_id = wait.last_event_id

  if wait.run_deadline_reached:
    search_abort_all_agent_sessions(run_id, "run budget exhausted")
    break

  for terminal event in wait.events:
    verify submitted candidate if present
    active_count = wait.active_count

  if wait.timed_out:
    inspect search_list_agent_status(run_id)
    request finalize or abort sessions that are stale/over budget
```

### Step 6: Subagent Contract

The subagent receives `agent_session_id` and must call:

```text
search-runtime_search_get_agent_context(agent_session_id="<agent_session_id>")
```

The returned context is authoritative. The subagent must use `context.run_id`, `context.candidate_id`, `context.agent_session_id`, and `context.workspace`; it must not use any run id, candidate id, or workspace path from the launch prompt.

It submits with values from context:

```json
{
  "run_id": "context.run_id",
  "candidate_id": "context.candidate_id",
  "artifact": {
    "candidate_id": "context.candidate_id",
    "agent_session_id": "context.agent_session_id",
    "status": "patch_ready",
    "summary": "what was tried and why",
    "next_ideas": []
  }
}
```

The subagent then calls `search_finish_agent_session`. If it cannot produce code, it should submit `abandoned` or finish with `failed`; it must not keep exploring past its deadline.

### Step 7: Verify, Select, Report

For every submitted candidate:

```text
search-runtime_search_run_verifier(run_id, candidate_id, "process")
```

Then:

```text
search-runtime_search_list_history(run_id, top_n=5, sort_by="score")
search-runtime_search_select(run_id)
search-runtime_search_report(run_id)
```

Show the user the selected candidate, score table summary, and report path.

### Step 8: Promote

Only after selection and user review:

```text
search-runtime_search_promote(run_id, selected_candidate_id)
```

Promotion exports a patch and should not directly mutate the main source workspace.

## Failure Handling

| Failure | Action |
|---|---|
| MCP tools unavailable | Tell the user the `search-runtime` MCP server is not connected; do not proceed |
| Freeze fails | Fix spec paths/artifacts, then retry freeze |
| Candidate workspace missing | Call status/report; do not recreate by hand |
| Pool is full | Wait for events or abort/finalize sessions; do not exceed `max_parallel` |
| Session deadline reached | Request finalize or abort the session; do not wait indefinitely |
| Run deadline reached | Abort all active sessions and report submitted candidates |
| Verifier fails | Keep the failure in report; do not edit verifier |
| No passing candidates | Report scores and failure classes; ask whether to run another batch |
| User wants to stop | Call `search-runtime_search_abort_all_agent_sessions` then `search-runtime_search_abort` |

## k_module Smoke Pattern

For a quick runtime smoke test, load `examples/k_module_search_spec.json`, freeze `tests/fixtures/k_module_problem/evaluator.py`, create 4 candidates, submit deterministic edits, verify, select, and report. This is a control-plane test, not a proof of search quality.

## Multi-Batch Examples

The bundled `circle_packing` and `signal_processing` specs use `agent-session-pool`. For `max_parallel=2` and 4 total subagents, plan/start 2 candidates first, supervise them through the wait loop, then plan/start the next 2 after slots free. At run budget exhaustion, abort active sessions and report the best submitted candidates.
