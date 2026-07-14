# Design

Goal Plus is a durable goal state machine with a verifier-backed Search engine.
It is deliberately not a general agent scheduler: the runtime owns evidence and
artifacts; hosts own live workers; the main agent owns policy.

## Architecture

```text
host agent
  -> GoalPlusTools -> FileGoalPlusRuntime
  -> SearchTools   -> FileSearchRuntime
                         |
                         +-> frozen specs and verifier artifacts
                         +-> candidate workspaces and Git commits
                         +-> plans, iterations, selection, reports, patches

host adapter / supervisor
  -> launches and waits for native workers
  -> enforces worker deadlines
  -> stores host-local lifecycle state outside Search records
```

Codex 0.144.1+ connects Goal Plus gates to `UserPromptSubmit`, `SessionStart`,
`PreToolUse`, `PostToolUse`, `Stop`, and `SubagentStop`. Pi implements the same
product flow with extension events and a durable local worker supervisor.

## Ownership Boundary

| Runtime owns | Host owns | Main agent owns |
|---|---|---|
| goal records and revisions | worker launch and termination | triage and spec discovery |
| frozen specs and hashes | wait-any and live status | candidate/continuation policy |
| workspace materialization | time, turn, or step enforcement | final verification and drain |
| verifier execution and history | native logs/transcripts | selection, report, promotion |
| selection/report/patch artifacts | host handles | full-goal audit |

Host pool state must not be copied into Search lifecycle fields. The Pi pool is
therefore stored under `.gp/host-pools/pi/`, separate from run records.

## Durable Model

```text
.gp/
  goal-plus/<goal_plus_id>/
    goal.json
    events.jsonl
  specs/<frozen_spec_id>/
    frozen_spec.json
    verifier-artifacts/
  runs/<run_id>/
    run.json
    plans/<plan_id>.json
    candidates/<candidate_id>/
    agent_sessions/<agent_session_id>.json
    workspace/<candidate_id>/
    report.md
    promotion/<candidate_id>.patch
  host-pools/pi/
```

The core records are:

- `GoalPlusRecord`: one complete user goal and its revision history.
  `search_tasks` is append-only; `linked_search` is a compatibility view.
- `SearchSpec`: immutable evaluation contract, edit surface, workspace policy,
  budget, strategy, and verifier commands.
- `SearchPlan`: one planning decision/round.
- `CandidateTask`: one isolated candidate workspace and its work order.
- `AgentSessionRecord`: context/provenance plus a host launch payload; never a
  process lifecycle record.
- `IterationRecord`: verifier result, failure, metrics, changed files, session
  provenance, and exact candidate Git commit.

## Invariants

1. **Freeze before search.** Ranking verifiers must pass preflight in a
   disposable workspace before a bundle is written.
2. **Freeze the evaluation contract.** Verifier artifacts are hash-pinned;
   candidate code cannot edit them.
3. **Isolate verifier side effects.** Every verifier invocation receives a
   unique temporary directory. Writes to the candidate workspace outside the
   declared outputs are infrastructure failures.
4. **Keep candidates isolated.** `git_worktree` is the default backend; `copy`
   remains available for fully independent snapshots.
5. **Commit verifier-backed states.** Candidate edits are committed before an
   iteration is recorded, so selection can restore the exact best state.
6. **Final verification outranks worker claims.** The main agent re-verifies
   ranked commits before selecting one.
7. **Do not silently mutate source.** Promotion exports a patch.
8. **Make limits explicit.** `max_candidates` is the whole-run workspace cap;
   `max_parallel` is the live-worker cap; a worker budget controls one host
   dispatch.

## Host Pool Contract

`HostPoolContract` describes mechanics without implementing policy:

| Field | Meaning |
|---|---|
| `launch_mode` | synchronous or asynchronous launch |
| `wait_mode` | how the parent receives completion, currently wait-any for Codex/Pi |
| `continuation_mode` | same-worker continuation or state redispatch |
| `deadline_mode` | which host component enforces the dispatch budget |
| `recovery_mode` | where live-pool discovery survives interruption |
| `completion_stage` | point at which a candidate-ready event is valid |

The main agent interprets events and chooses the next action. A host supervisor
may persist jobs, but it must not plan candidates or auto-refill slots.

## Strategy And Budget

`agent_guided`/`agent`/`default` consume main-agent proposals.
`random`/`random_mode` consume runtime work orders. These are the portable
strategies for Pi, Codex, and Claude Code. Existing advanced strategies and
trace export remain OpenCode compatibility paths.

`search_plan_next` persists a round and plans:

```text
min(requested_k, remaining max_candidates, max_parallel)
```

Rolling execution means this planned set is not a completion barrier. A newly
free slot can be used as soon as policy and remaining budget justify it.

## Scope Boundary

Goal Plus does not own nested search graphs, hardware scheduling, or domain
concepts such as GPU topology. Scenario-local harnesses may allocate resources
and record opaque evidence. Search verifiers rank candidates; the final Goal
Plus audit proves the original user goal after integration.

## Modules

| Path | Responsibility |
|---|---|
| `src/goal_plus/goal_plus.py` | goal state machine and gates |
| `src/goal_plus/runtime.py` | Search state, verification, selection, promotion |
| `src/goal_plus/workspaces.py` | candidate materialization |
| `src/goal_plus/agent_hosts.py` | host launch/continue/pool capabilities |
| `src/goal_plus/agent_pool.py` | shared pool contract and events |
| `src/goal_plus/pi_pool.py` | durable Pi host supervisor |
| `src/goal_plus/server.py` | MCP surface |

For the operational sequence, read [Flow](flow-view.md). For exact tools, read
[API](api.md).
