---
name: search
description: >
  Run MCP-controlled Search Mode for measurable multi-candidate coding tasks.
  Use when the user asks to try several candidate fixes,
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

`OPENCODE_EXPERIMENTAL=true` also enables the same flag. This environment variable belongs to the OpenCode process itself, not only to the MCP server subprocess configured in `opencode.json`.

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
| `search_start_agent_session` | `search-runtime_search_start_agent_session` |
| `search_get_agent_context` | `search-runtime_search_get_agent_context` |
| `search_update_agent_status` | `search-runtime_search_update_agent_status` |
| `search_list_agent_status` | `search-runtime_search_list_agent_status` |
| `search_finish_agent_session` | `search-runtime_search_finish_agent_session` |
| `search_abort_agent_session` | `search-runtime_search_abort_agent_session` |
| `search_abort_all_agent_sessions` | `search-runtime_search_abort_all_agent_sessions` |
| `search_publish_observation` | `search-runtime_search_publish_observation` |
| `search_list_observations` | `search-runtime_search_list_observations` |
| `search_wait_agent_events` | `search-runtime_search_wait_agent_events` |
| `search_submit_candidate` | `search-runtime_search_submit_candidate` |
| `search_run_verifier` | `search-runtime_search_run_verifier` |
| `search_list_iterations` | `search-runtime_search_list_iterations` |
| `search_select` | `search-runtime_search_select` |
| `search_report` | `search-runtime_search_report` |
| `search_promote` | `search-runtime_search_promote` |

If these tools are unavailable, stop and report that the MCP server is not connected. Do not simulate runtime state in chat.

## Required Discipline

1. Do not start candidate execution before freezing the SearchSpec and verifier artifacts.
2. Do not modify verifier files during candidate execution.
3. Do not edit the main source workspace while exploring candidates.
4. Subagents self-verify via `search_run_verifier` with their own `agent_session_id`. After session termination, call `search_run_verifier` yourself (without `agent_session_id`) to confirm the final score against the best-so-far workspace state.
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
    "history_policy": {
      "scope": "top_n",
      "top_n": 5
    }
  }
}
```

`max_candidates` and `max_parallel` are enforced by the runtime. `wall_clock_seconds` is the run budget. `worker_timeout_seconds` is the default MCP session budget, capped by remaining run time. It is not an OpenCode `Task` parameter. Prefer small worker budgets for exploratory examples; use larger values only when the verifier/task genuinely needs them.

`strategy.worker_mode` must be `agent-session-pool` (the only supported value). Retired values `main-agent-search-direct`, `auto`, and `sub-agent-search-dispatch` are normalized to `agent-session-pool` at parse time, so legacy specs still load.

`strategy.worker_agent_type` selects the OpenCode subagent variant, which fixes the per-session step cap:

| Variant | Steps | Use when |
|---|---|---|
| `AnySearchAgentFlash` | 15 | Smoke tests, cheap iterations |
| `AnySearchAgent` (default) | 50 | Standard autoresearch loop |
| `AnySearchAgentDeep` | 100 | Sustained iteration on harder problems |
| `AnySearchAgentExtraDeep` | 150 | Extensive search, complex fixtures |

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

Each returned `CandidateTask` owns an isolated workspace. Candidate work must stay inside that workspace and only modify allowed files.

### Step 5: Dispatch Autoresearcher Sessions

For `worker_policy.mode == "agent-session-pool"` (the only supported mode):

1. Start at most `budget.max_parallel` sessions.
2. For each candidate, call `search-runtime_search_start_agent_session(run_id, candidate_id, directive, budget)` to get `agent_session_id`.
3. Launch the subagent with `Task(subagent_type="AnySearchAgent", prompt="<agent_session_id>; candidate idea: <one paragraph>")`. **This is the actual worker launch — `search_start_agent_session` only registers an MCP-side session record; without a matching `Task` call, no worker process runs, the session stays idle, and `search_wait_agent_events` will block until `worker_timeout_seconds` elapses with zero real work done.** Call Task in the **same model turn** as the `search_start_agent_session` that produced the `agent_session_id`, never in a later turn — otherwise the host will already be blocked in `wait_agent_events` while no worker exists.
4. **If `budget.max_parallel == 1`**, foreground Task is fine — main blocks on the worker, no supervisor loop needed. Skip to step 7 when it returns.
5. **If `budget.max_parallel > 1`**, Task must include `background: true` (requires `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true` on the OpenCode process). No `timeout` field exists on Task; the MCP supervisor loop enforces deadlines.
6. Each AnySearchAgent runs an autoresearch-style loop inside its workspace: it self-iterates, calls `search_run_verifier` with its own `agent_session_id`, tracks git commits, and maintains a local `results.tsv`. You do not supervise iteration-level progress.
7. For parallel runs, enter a supervisor loop with `search-runtime_search_wait_agent_events(run_id, timeout_seconds=<poll window>, since_event_id=<last seen>)` to wake on terminal events.
8. When a session terminates (completed / failed / aborted / timed_out), run `search-runtime_search_run_verifier(run_id, candidate_id, "process")` yourself to confirm the final score.
9. If slots free and candidate budget remains, plan and start the next batch. Read `search-runtime_search_list_observations(run_id, top_n=20)` to inform the next plan when useful.
10. On run deadline, call `search-runtime_search_abort_all_agent_sessions(run_id)` before reporting.

Hard host rule:

- For `max_parallel > 1`, Task input must include `background: true`. A normal OpenCode `Task` call without `background` blocks the main agent and prevents supervision.
- For `max_parallel == 1`, foreground Task is acceptable — there is nothing else to wait on, and the supervisor loop would be pure overhead.
- There is no supported `timeout` field on Task; use MCP session deadlines and the supervisor loop instead.
- The Task prompt must not hard-code `run_id`, `candidate_id`, or workspace paths for the worker to use. The worker must derive them from `search_get_agent_context(agent_session_id)`. Human-readable candidate ideas are fine; authoritative identifiers and paths come only from MCP context.
- Seeing `AnySearchAgent Task — ...` followed by worker tool activity, with no immediate `search_wait_agent_events` call from the main agent, means the main agent is blocked in foreground Task execution. This is fine for `max_parallel == 1` and a bug for `max_parallel > 1`.
- Do not launch foreground Task calls "to see if they block". If foreground Task is the only available subagent mechanism, do not use `agent-session-pool` subagents for that run.

Supervisor loop sketch:

```text
last_event_id = null
pending_candidates = [...]
while pending_candidates or active_sessions:
  while pending_candidates and active_count < max_parallel:
    session = search_start_agent_session(...)
    Task(subagent_type="AnySearchAgent", background=true,
         prompt=f"agent_session_id={session.agent_session_id}; {idea}")
    active_count += 1

  wait = search_wait_agent_events(run_id, timeout_seconds=300, since_event_id=last_event_id)
  last_event_id = wait.last_event_id

  if wait.run_deadline_reached:
    search_abort_all_agent_sessions(run_id, "run budget exhausted")
    break

  for terminal event in wait.events:
    search_run_verifier(run_id, event.candidate_id, "process")  # main-side final confirm
    active_count = wait.active_count

  if not pending_candidates and budget_remaining and active_count == 0:
    observations = search_list_observations(run_id, top_n=20)
    plan = search_plan_next(run_id, requested_k=k)
    tasks = search_start_batch(run_id, plan.plan_id)
    pending_candidates = [t.candidate_id for t in tasks]

  if wait.timed_out:
    inspect search_list_agent_status(run_id)
    request finalize or abort sessions that are stale/over budget
```

### Step 6: Subagent Autoresearch Contract

The subagent receives only `agent_session_id` and a candidate idea. It then:

1. Calls `search-runtime_search_get_agent_context(agent_session_id)` to read authoritative `run_id`, `candidate_id`, `workspace`, `allowed_files`, `denied_files`, `budget`, `history`, `observations`, and `iterations` (its own previous attempts).
2. Runs an autoresearch loop inside `workspace`: edit allowed files → `search-runtime_search_run_verifier(..., agent_session_id=...)` → read ScoreReport → `git commit` (improvement) or `git reset --hard HEAD~1` (regression). Each verifier call appends to the candidate's iteration history; no separate `submit_candidate` step is needed.
3. Maintains `workspace/.tmp/results.tsv` as its private iteration log.
4. Calls `search-runtime_search_finish_agent_session(agent_session_id, status, summary, result)` when done, with the best score and iteration count.

You do not pass numeric score targets, baseline scores, or local-verification requests in the worker prompt. The worker reads its own verifier output and decides next steps.

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
| User wants to stop | Call `search-runtime_search_abort_all_agent_sessions(run_id)` to cancel all active sessions; no separate run-level abort tool exists |

## k_module Smoke Pattern

For a quick runtime smoke test, load `examples/k_module_search_spec.json`, freeze `tests/fixtures/k_module_problem/evaluator.py`, create 4 candidates, submit deterministic edits, verify, select, and report. This is a control-plane test, not a proof of search quality.

## Multi-Batch Examples

The bundled `circle_packing` and `signal_processing` specs use `agent-session-pool`. For `max_parallel=2` and 4 total subagents, plan/start 2 candidates first, supervise them through the wait loop, then plan/start the next 2 after slots free. At run budget exhaustion, abort active sessions and report the best submitted candidates.
