# Plan: OpenCode-Native Subagent Flow, No MCP Lifecycle Control

Date: 2026-06-30

Status: ready for implementation by the next agent.

Owner intent: remove the old MCP-controlled subagent lifecycle entirely. Do not keep compatibility tools, "legacy" aliases, sqlite host-sync, finalize APIs, heartbeat APIs, MCP wait loops, or MCP abort/status control. The purpose is to avoid the main agent or subagent accidentally using the old unreliable design.

## 1. Problem

The current codebase still exposes a supervisor model where the MCP runtime pretends to own subagent lifecycle state:

- `search_wait_agent_events`
- `search_update_agent_status`
- `search_list_agent_status`
- `search_finish_agent_session`
- `search_abort_agent_session`
- `search_abort_all_agent_sessions`
- `search_submit_candidate`
- `search_publish_observation`
- `search_list_observations`
- `search_next_batch`
- OpenCode sqlite host-sync via `--opencode-db`

This creates two sources of truth:

- OpenCode owns the actual `Task` process, child session, step cap, and return value.
- The Python MCP runtime owns a duplicate `AgentSessionRecord.status` state machine.

The duplicate state is unreliable. Example failure: OpenCode stops a child session after step cap, but MCP state remains `RUNNING` unless the worker remembered to call a finish tool or the runtime observes sqlite. The sqlite sync workaround is also wrong as the main design because it requires the external MCP process to reverse-engineer OpenCode internals.

The corrected design is:

- OpenCode owns subagent lifecycle.
- MCP owns candidate workspaces, immutable verifier execution, scoring history, reports, and promotion patches.
- `search_start_agent_session` only creates a context/provenance handle for a candidate.
- A subagent only needs two MCP calls:
  - `search_get_agent_context(agent_session_id)`
  - `search_run_verifier(run_id, candidate_id, scope="process", agent_session_id=agent_session_id)`
- Main agent uses OpenCode `Task` return values to know when workers finish. It does not call MCP `wait`.

## 2. Ground Truth From OpenCode

The implementation plan depends on these OpenCode facts already verified in `/Users/qiaolina/Code/oh-my-knowledge/code/opencode`:

- `packages/opencode/src/tool/task.ts`
  - `TaskTool` creates a child session with `parentID: ctx.sessionID`.
  - Task metadata contains `parentSessionId` and `sessionId`.
  - Task metadata contains the child `sessionId`.
- `packages/opencode/src/session/status.ts`
  - `/session/status` is only `busy/idle/retry`; it is not a durable completed/error ledger.
- `packages/opencode/src/mcp/catalog.ts`
  - OpenCode's MCP client calls external MCP tools with only model-supplied `arguments`; it does not automatically pass OpenCode's parent session or Task lifecycle objects to the MCP server.

Conclusion: use OpenCode's native Task return as the lifecycle signal inside the main agent. Do not rebuild lifecycle observation in the Python MCP server.

## 3. Target Flow

### 3.1 Main Agent Flow

```text
Main:
  search_freeze_spec(spec, verifier_artifact_paths)
  search_create(frozen_spec_id) -> run_id
  search_plan_next(run_id, requested_k) -> plan
  search_start_batch(run_id, plan_id, proposals?) -> CandidateTask[]

  for each candidate to launch:
    search_start_agent_session(run_id, candidate_id, directive?) -> AgentSessionRecord
    Task(
      subagent_type=<plan.worker_policy.worker_agent_type or AnySearchAgent>,
      description=<launch.description from start_agent_session>,
      prompt=<launch.prompt from start_agent_session>
    )

  OpenCode, not MCP, tells Main when Task finishes.

  for each finished Task/candidate:
    search_run_verifier(run_id, candidate_id, scope="process")

  if budget remains:
    plan/start next batch

  finally:
    search_select(run_id)
    search_report(run_id)
    search_promote(run_id, candidate_id) if requested
```

Important:

- There is no MCP wait loop.
- There is no MCP abort for subagent processes.
- There is no MCP finish/finalize call.
- Main must not start a session and then wait on MCP. The real worker is the OpenCode `Task` call.
- Task calls are foreground calls. `max_parallel` remains a planning hint, not a runtime lifecycle feature.

### 3.2 Subagent Flow

```text
Subagent:
  receives only agent_session_id plus a human-readable candidate idea
  search_get_agent_context(agent_session_id)
  cd context.workspace

  loop until OpenCode step cap / self decision:
    edit allowed files
    git commit candidate iteration
    search_run_verifier(
      run_id=context.run_id,
      candidate_id=context.candidate_id,
      scope="process",
      agent_session_id=context.agent_session_id
    )
    keep good commits, reset bad commits
    update workspace/.tmp/results.tsv

  leave best workspace state checked out
  final answer: concise text summary only
```

Subagent must not call any lifecycle/status/finalize/submit/observation tool because those tools will no longer exist.

## 4. Target Public MCP Tool Surface

After cleanup, these are the only public MCP tools:

1. `search_freeze_spec`
2. `search_create`
3. `search_status`
4. `search_list_history`
5. `search_plan_next`
6. `search_start_batch`
7. `search_start_agent_session`
8. `search_get_agent_context`
9. `search_run_verifier`
10. `search_list_iterations`
11. `search_select`
12. `search_report`
13. `search_promote`

No compatibility tools. No deprecated aliases. No hidden "legacy" path in docs.

### 4.1 Delete These Public Tools

Remove these from `server.py`, `tools.py`, docs, tests, prompts, and expected tool lists:

- `search_next_batch`
- `search_update_agent_status`
- `search_list_agent_status`
- `search_finish_agent_session`
- `search_abort_agent_session`
- `search_abort_all_agent_sessions`
- `search_publish_observation`
- `search_list_observations`
- `search_wait_agent_events`
- `search_submit_candidate`

### 4.2 Remove OpenCode sqlite Sync

Remove:

- `--opencode-db`
- `AGENTIC_ANY_SEARCH_OPENCODE_DB`
- `sqlite3` import in runtime
- `FileSearchRuntime(..., opencode_db_path=...)`
- `sync_host_agent_sessions`
- `_observe_opencode_session`
- `_finish_agent_session_from_host`
- `_host_observation_reason`
- `AgentSessionRecord.host`
- docs that mention sqlite host sync

Rationale: sqlite sync was a workaround for the old duplicate state machine. The new design has no MCP lifecycle status to sync.

## 5. Data Model Cleanup

Edit `src/agentic_any_search_mcp/models.py`.

### 5.1 Remove Lifecycle State Models

Delete:

- `AgentSessionStatus`
- `AgentSessionPhase`
- `TERMINAL_AGENT_SESSION_STATUSES`
- `AgentSessionBudget`
- `AgentSessionEvent`
- `AgentObservation`
- `AgentSessionWaitResult`

Delete `VisibilityMode` only if nothing else needs it after observation removal. If it remains unused, remove it.

### 5.2 Simplify `AgentSessionRecord`

Replace the current lifecycle-heavy shape with a context/provenance handle:

```python
class AgentSessionRecord(SearchModel):
    agent_session_id: str
    run_id: str
    candidate_id: str
    created_at: str
    updated_at: str
    directive: dict[str, Any] = Field(default_factory=dict)
    workspace: Path
    launch: dict[str, Any] = Field(default_factory=dict)
    counters: dict[str, int] = Field(default_factory=dict)
```

Notes:

- `candidate_id` should become required. A subagent session without a candidate has no useful role in this runtime.
- Remove `status`, `phase`, `last_heartbeat_at`, `budget`, `current_goal`, `last_action`, `next_step`, `blockers`, `summary`, `result`, `host`.
- `launch` should contain the OpenCode Task copy/paste fields generated by `start_agent_session`:
  - `subagent_type`
  - `description`
  - `prompt`

### 5.3 Simplify `RunRecord`

Remove counters only needed by deleted event/observation systems:

- `next_agent_event_index`
- `next_observation_index`

Keep:

- `next_agent_session_index`

### 5.4 Simplify `RunSummary`

Remove:

- `candidates_running`

It was tied to MCP active session state. Use evaluated count and budget counters instead.

### 5.5 Simplify Candidate Artifact State

Remove `ArtifactBundle` if no remaining production code needs it after `search_submit_candidate` deletion.

Then simplify `CandidateRecord`:

```python
class CandidateRecord(SearchModel):
    candidate_id: str
    status: Literal["created", "evaluated", "failed"]
    task: CandidateTask
    detected_changed_files: list[str] = Field(default_factory=list)
    touched_denied_files: bool = False
    changed_outside_allowed: bool = False
    score_report: ScoreReport | None = None
    iterations: list[IterationRecord] = Field(default_factory=list)
```

Remove:

- `"submitted"` status
- `artifact`
- artifact validator

Rationale: verifier runs already detect changed files and score candidate workspaces. There is no separate submit step.

### 5.6 Remove Legacy Spec Normalization

Because the user explicitly requested no legacy paths, remove compatibility validators:

- `StrategySpec.worker_mode_accepts_legacy_dispatch`
- `SearchSpec.strategy_accepts_legacy_string`

After cleanup:

- `strategy` must be structured.
- `worker_mode` must already be `"agent-session-pool"`.

## 6. Runtime Cleanup

Edit `src/agentic_any_search_mcp/runtime.py`.

### 6.1 Constructor

Change:

```python
def __init__(self, root_dir: Path | str = ".search") -> None:
```

Remove:

- `opencode_db_path`
- `OPENCODE_DB_ENV`
- sqlite/env resolution

### 6.2 `status`

Remove active session count from summary. If `RunSummary` still needs budget info, keep:

- `run_id`
- `state`
- `frozen_spec_id`
- `candidates_total`
- `candidates_evaluated`
- `best_candidate_id`
- `best_score`
- `budget_used`

No `candidates_running`.

### 6.3 `next_batch`

Delete `next_batch`.

It is a compatibility shortcut and should not remain public or documented. The only flow is:

```text
search_plan_next -> search_start_batch
```

### 6.4 `start_agent_session`

Change the method from lifecycle admission control to context-handle creation.

New behavior:

1. Load run and candidate.
2. Require `candidate_id`.
3. Do not call `_active_agent_session_count`.
4. Do not enforce `max_parallel`.
5. Allocate `agent_session_id`.
6. Build a launch payload:

```python
launch = {
    "subagent_type": frozen.spec.strategy.worker_agent_type or "AnySearchAgent",
    "description": f"{candidate_id} {short_intent}",
    "prompt": (
        f"agent_session_id={agent_session_id}; "
        f"candidate_id={candidate_id}; "
        f"idea: {one_paragraph_directive_or_task_intent}"
    ),
}
```

Discussion:

- Earlier docs said never include `candidate_id` in prompt. That rule was to prevent the worker from trusting prompt-supplied ids. In the new plan the worker still must derive authoritative `run_id/candidate_id/workspace` from `search_get_agent_context`; including `candidate_id` in the prompt/description is only for the main agent and OpenCode UI to map Task return back to the candidate.
- Tool descriptions and subagent prompts must say: prompt identifiers are labels only; context is authoritative.

7. Write `AgentSessionRecord`.
8. Return the full record including `launch`.

### 6.5 `get_agent_context`

Update returned context:

Keep:

- `agent_session_id`
- `run_id`
- `candidate_id`
- `directive`
- `workspace`
- `objective`
- `metric_name`
- `metric_direction`
- `run_budget`
- `candidate_task`
- `history`
- `iterations`

Remove:

- `status`
- `phase`
- `visibility_mode`
- `budget`
- `peer_status`
- `observations`

Rationale: subagent gets one initial context and then verifier feedback. No cross-agent status or observation bus.

### 6.6 `run_verifier`

Keep `agent_session_id` optional because:

- Subagent self-score calls must pass it.
- Main final-confirm calls should omit it.

Update behavior:

- If `agent_session_id` is provided, validate that it belongs to the candidate and record it on `IterationRecord`.
- If it is omitted, do not auto-attribute to a session. The current fallback that guesses the unique candidate session should be removed because it hides prompt mistakes.
- Increment `session.counters["verifier_runs"]` when `agent_session_id` is provided.
- Do not reference session status.
- Candidate status transitions should be `created -> evaluated`; no `submitted`.

### 6.7 Delete Lifecycle/Observation Methods

Delete these methods and any helpers that only support them:

- `update_agent_status`
- `list_agent_status`
- `sync_host_agent_sessions`
- `_observe_opencode_session`
- `_finish_agent_session_from_host`
- `_host_observation_reason`
- `finish_agent_session`
- `abort_agent_session`
- `abort_all_agent_sessions`
- `_abort_agent_session_record`
- `publish_observation`
- `list_observations`
- `wait_agent_events`
- `_active_agent_session_count`
- `_append_agent_event`
- `_write_agent_event`
- `_load_agent_events`

Also remove filesystem directories for new runs:

- `agent_events/`
- `observations/`

Do not attempt to migrate old `.search` runs. This cleanup targets new runs and tool behavior.

## 7. Tools Layer Cleanup

Edit `src/agentic_any_search_mcp/tools.py`.

Keep only these methods:

- `search_freeze_spec`
- `search_create`
- `search_status`
- `search_list_history`
- `search_plan_next`
- `search_start_batch`
- `search_start_agent_session`
- `search_get_agent_context`
- `search_run_verifier`
- `search_list_iterations`
- `search_select`
- `search_report`
- `search_promote`

Remove:

- `ArtifactBundle` import
- `_agent_session_ack`
- every deleted public tool listed in section 4.1

`search_start_agent_session` should return the simplified record including `launch`.

## 8. MCP Server Cleanup

Edit `src/agentic_any_search_mcp/server.py`.

### 8.1 Constructor/CLI

Change:

```python
def create_mcp(root_dir: str | Path = ".search") -> FastMCP:
    runtime = FileSearchRuntime(root_dir)
```

Remove CLI argument:

- `--opencode-db`

Main should call:

```python
create_mcp(args.root).run(transport="stdio")
```

### 8.2 Registered Tools

Register only the 13 target tools in section 4.

Important docstrings:

- `search_start_agent_session`: "Creates a context/provenance handle and returns OpenCode Task launch fields. It does not start a worker and does not track lifecycle."
- `search_get_agent_context`: "Subagent first call. Authoritative ids and workspace."
- `search_run_verifier`: "Subagent self-score with `agent_session_id`; main final verify without it."

Do not mention:

- wait
- heartbeat
- finalize
- abort
- submit
- sqlite
- host sync
- legacy

## 9. OpenCode Config Cleanup

Edit `opencode.json`.

Remove:

```json
"--opencode-db",
"~/.local/share/opencode/opencode.db"
```

Expected command:

```json
[
  "agentic-any-search-mcp",
  "--root",
  ".search"
]
```

## 10. Prompt And Skill Cleanup

Update all OpenCode-facing instructions so the old APIs cannot be rediscovered by an agent.

### 10.1 `.opencode/skills/search/SKILL.md`

Rewrite the workflow around OpenCode-native Task lifecycle.

Must say:

- MCP does not supervise subagent lifecycle.
- `search_start_agent_session` creates a context/provenance handle and returns Task launch fields.
- Main launches OpenCode Task immediately with those launch fields.
- Main waits for OpenCode Task return, not MCP wait.
- Subagent has exactly two MCP calls:
  - `search_get_agent_context`
  - `search_run_verifier`
- Main final-confirms with `search_run_verifier` after OpenCode Task returns.
- There is no MCP abort; stopping a running subagent is an OpenCode/user interruption concern.

Must remove all mentions of:

- `search_wait_agent_events`
- `search_update_agent_status`
- `search_list_agent_status`
- `search_finish_agent_session`
- `search_abort_agent_session`
- `search_abort_all_agent_sessions`
- `search_submit_candidate`
- `search_publish_observation`
- `search_list_observations`
- host sync
- sqlite
- finalize
- heartbeat
- supervisor loop

### 10.2 `.opencode/agents/search-orchestrator.md`

Rewrite the orchestrator bullets:

- plan/start batches
- call `search_start_agent_session`
- copy returned launch payload into Task
- use OpenCode Task return
- final-verify completed candidate
- no MCP wait/status/abort/finalize

### 10.3 `.opencode/agents/AnySearchAgent*.md`

For all variants:

- "The only required MCP calls are `search_get_agent_context` and `search_run_verifier`."
- Remove "host/runtime sync records terminal state".
- Remove any mention of no finish tool if it references the deleted tool by name.
- Remove observation/status/finalize language.
- Keep `results.tsv`, git commit-first, metric_name, and edit-surface discipline.
- Final answer should include:
  - `agent_session_id`
  - `candidate_id`
  - best score/metric
  - best commit hash
  - changed files
  - short summary

This final answer is for OpenCode/main-agent mapping, not MCP lifecycle.

## 11. Docs Cleanup

Update docs to describe one coherent current design. Do not preserve old alternatives.

### 11.1 `docs/flow-view.md`

Make this the canonical main/subagent interaction doc.

New sections:

1. Two agents, one OpenCode lifecycle.
2. Single-batch flow:
   - freeze/create
   - plan/start_batch
   - start_agent_session creates launch payload
   - OpenCode Task starts worker
   - subagent context + verifier loop
   - OpenCode Task return
   - main final verify/select/report
3. What subagent sees:
   - context fields
   - verifier reports
   - its own `results.tsv`
4. What subagent does not see:
   - peer status
   - peer workspace
   - lifecycle state
   - MCP event queue
5. Removed APIs:
   - list deleted tools and say they are intentionally absent.
6. Constraints:
   - no Task timeout
   - no MCP process cancellation
   - main must use OpenCode Task result as completion signal

### 11.2 `docs/design.md`

Update architecture:

- Runtime owns specs, plans, workspaces, verifier scoring, history, reports, promotion.
- OpenCode owns subagent lifecycle.
- Remove "supervisor loop" and "agent-session events".
- `AgentSessionRecord` is a context/provenance handle.
- Remove state flow steps involving `wait_agent_events`, abort, finish, host sync.
- Remove "legacy worker mode normalized" language.
- Remove `search_next_batch` compatibility helper.

### 11.3 `docs/opencode.md`

Update:

- command has no `--opencode-db`
- expected MCP tools list has only 13 tools
- agent session section uses OpenCode Task return
- no supervisor wait loop
- no abort-all before report
- no sqlite troubleshooting

### 11.4 `docs/toy-example.md`

Update:

- no `search_next_batch`
- no `search_submit_candidate`
- no `search_wait_agent_events`
- no `search_list_agent_status`
- no sqlite troubleshooting
- example sequence uses OpenCode Task return.

### 11.5 `docs/debugging-runtime.md`

Rewrite around the new boundary:

- Debug candidate workspace, verifier logs, iterations, reports.
- Debug OpenCode Task lifecycle in OpenCode, not MCP.
- Remove sqlite DB instructions.
- Remove agent event/session status troubleshooting.
- Keep file layout for `.search/runs/<run_id>/workspace`, candidates, verifier logs, report, promotion.

## 12. Tests Cleanup

Run tests frequently while deleting. The current test suite asserts many old APIs exist.

### 12.1 `tests/test_server.py`

Update expected tool list to exactly the 13 target tools.

Remove fake methods for deleted tools.

Update constructor test:

- no `opencode_db_path`
- no `--opencode-db`

Keep or add:

- `test_create_mcp_registers_only_opencode_native_tools`
- `test_run_verifier_exposes_optional_agent_session_id`
- `test_start_agent_session_returns_launch_payload`

### 12.2 `tests/test_tools.py`

Remove delegation checks for deleted tools.

Keep checks for:

- freeze/create/status/history
- plan/start_batch
- start_agent_session
- get_agent_context
- run_verifier
- list_iterations
- select/report/promote

### 12.3 `tests/test_runtime_unit.py`

Delete tests for:

- sqlite host sync
- status update
- status list
- wait loop
- abort enforcement
- finish/finalize
- observation publish/list
- submit candidate
- active session pool full based on `RUNNING`
- auto-attribution of verifier calls to a unique session

Add/update tests:

1. `test_start_agent_session_creates_context_handle_and_launch_payload`
   - Create run, candidate.
   - Call `start_agent_session`.
   - Assert record has `agent_session_id`, `candidate_id`, `workspace`.
   - Assert `launch.prompt` contains `agent_session_id`.
   - Assert `launch.description` includes candidate id or intent.

2. `test_start_agent_session_does_not_enforce_active_pool_status`
   - With `max_parallel=1`, create two candidate sessions for two candidates if budget allows.
   - This verifies MCP no longer deadlocks on stale RUNNING status.
   - Main/OpenCode is responsible for not launching too many simultaneous Tasks.

3. `test_get_agent_context_has_only_authoritative_worker_fields`
   - Assert no `peer_status`, no `observations`, no `status`, no `phase`.
   - Assert context includes candidate task, history, iterations.

4. `test_run_verifier_records_iteration_with_agent_session_id`
   - Verify `IterationRecord.agent_session_id` is set when passed.
   - Verify session counter increments.

5. `test_run_verifier_without_agent_session_id_is_main_final_verify`
   - Verify it succeeds and records an iteration with `agent_session_id=None`.
   - Verify no auto-attribution occurs.

6. `test_removed_runtime_methods_are_absent` if useful:
   - `assert not hasattr(runtime, "wait_agent_events")`
   - same for finish/update/abort/submit/observations if the team wants explicit guardrails.

### 12.4 `tests/test_models.py`

Remove imports/assertions for:

- `AgentSessionStatus`
- `AgentSessionPhase`
- `AgentSessionBudget`
- `AgentSessionEvent`
- `AgentSessionWaitResult`
- `AgentObservation`
- `ArtifactBundle` if deleted

Add simplified model validation tests:

- `AgentSessionRecord` requires `candidate_id`.
- `CandidateRecord.status` rejects `"submitted"`.
- `StrategySpec.worker_mode` rejects old values instead of normalizing.
- `SearchSpec.strategy` rejects string strategy instead of normalizing.

### 12.5 `tests/test_opencode_assets.py`

Update expected prompt contract:

- subagent only has two MCP calls.
- no deleted tool names appear in `.opencode/agents/*.md` or `.opencode/skills/search/SKILL.md`.
- `opencode.json` command does not include `--opencode-db`.

### 12.6 Scenario Tests

Update:

- `tests/test_example_scenarios.py`
- `tests/test_k_module_runtime.py`

Remove calls to:

- `search_submit_candidate`
- `search_finish_agent_session`
- `search_wait_agent_events`

Use `search_run_verifier` as the scoring/submission mechanism.

## 13. Acceptance Checks

Run:

```bash
python -m pytest -q
python -m compileall src tests
```

Then run guardrail searches.

These names should not appear in production source, prompts, or normal docs. It is acceptable for them to appear only in this plan file or in a changelog explicitly documenting removal.

```bash
rg -n "search_wait_agent_events|search_finish_agent_session|search_update_agent_status|search_list_agent_status|search_abort_agent_session|search_abort_all_agent_sessions|search_submit_candidate|search_publish_observation|search_list_observations|search_next_batch|--opencode-db|opencode_db|sync_host|host sync|sqlite" src tests docs .opencode opencode.json
```

Expected result after implementation:

- No matches outside `docs/superpowers/plans/2026-06-30-opencode-native-subagent-flow.md`.

Also check public tools:

```bash
python - <<'PY'
from agentic_any_search_mcp.server import create_mcp
mcp = create_mcp(".search")
print(sorted(mcp._tool_manager._tools))
PY
```

Expected exact list:

```text
search_create
search_freeze_spec
search_get_agent_context
search_list_history
search_list_iterations
search_plan_next
search_promote
search_report
search_run_verifier
search_select
search_start_agent_session
search_start_batch
search_status
```

If FastMCP internals differ, adapt the inspection to the current tool manager API, but the exact registered names must match.

## 14. Implementation Order

Recommended order for the next agent:

1. Update models first.
2. Update runtime to compile with simplified models.
3. Update tools layer.
4. Update MCP server and `opencode.json`.
5. Update tests for server/tools/runtime/models.
6. Run targeted tests and fix failures.
7. Update `.opencode` prompts and docs.
8. Run full pytest and compileall.
9. Run guardrail `rg`.

Reason: if docs are edited first, tests will still fail noisily on old APIs. Models/runtime/tools/server establishes the real API surface first.

## 15. Current Worktree Note

At the time this plan was written, the worktree already contains a previous partial implementation that added sqlite host sync and optional finalize compatibility language. The next agent should remove those changes by editing files, not by using `git reset`, because the worktree may also contain unrelated user changes.

Important current dirty files include:

- `src/agentic_any_search_mcp/runtime.py`
- `src/agentic_any_search_mcp/server.py`
- `src/agentic_any_search_mcp/models.py`
- `tests/test_runtime_unit.py`
- `tests/test_server.py`
- `tests/test_opencode_assets.py`
- `.opencode/agents/*.md`
- `.opencode/skills/search/SKILL.md`
- `docs/*.md`
- `opencode.json`

Preserve unrelated user edits. Remove only the old lifecycle-control design and sqlite workaround.

## 16. Non-Goals

Do not implement an OpenCode plugin or patch OpenCode itself in this cleanup.

Do not add a new HTTP bridge from Python MCP to OpenCode.

Do not keep deleted APIs as hidden aliases.

Do not preserve backward compatibility for old `.search` run files.

Do not invent MCP-side cancellation. If a user wants to stop running subagents, that should be handled through OpenCode/user interruption, not this MCP runtime.

## 17. Final Expected Mental Model

The MCP runtime is not a process supervisor.

It is a scoring and artifact runtime:

- freeze specs
- create candidate workspaces
- produce candidate plans
- hand subagents their workspace context
- run immutable verifier commands
- record iteration scores
- select/report/promote

OpenCode is the process supervisor:

- start Task
- run subagent
- enforce step cap
- stop/interrupt Task
- return Task result

If a future agent tries to reintroduce `wait_agent_events`, status heartbeat, finalize, abort, or sqlite host sync, that is a regression against this plan.
