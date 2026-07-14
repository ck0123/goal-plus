# Flow

This is the canonical end-to-end flow. Host documents only describe how a host
implements the worker-pool steps; [Design](design.md) owns data and invariants,
and [API](api.md) owns tool reference.

## Roles

| Role | Owns |
|---|---|
| Main agent | goal interpretation, triage, search policy, slot allocation, final verification, selection, reporting, promotion |
| Candidate worker | one candidate workspace, iterative edits, self-verification, concise research handoff |
| Search runtime | frozen specs, isolated workspaces, plans, verifier records, ranking, reports, patches |
| Host pool | worker launch, wait-any events, deadlines, interrupts, native transcripts |

The Search runtime never treats `AgentSessionRecord` as a live process. It is a
context and provenance handle only.

## Goal Plus Lifecycle

```text
user request
  -> goal_plus_create
  -> goal_plus_record_triage
       -> ordinary goal work
       -> spec discovery
            -> goal_plus_save_spec_draft
            -> search_freeze_spec
            -> search_create
            -> goal_plus_link_search_run
            -> rolling search loop
            -> search_select -> search_report -> search_promote
            -> goal_plus_record_search_result
  -> audit the full current goal revision
  -> optional independent final check
  -> terminal goal status
```

A Goal Plus record may contain multiple search tasks. Each task is one
`run_id` over one frozen spec. A later task is added only when the full-goal
audit finds another measurable subproblem; Search tasks are not nested.

## Exploration Guidance

`/goal-plus mode=autonomous <goal>` and `/goal-plus mode=probe <goal>` select
how the main agent initially spends worker leases. `autonomous` is the default:
initial workers should receive a meaningful window (about 15 minutes when the
host supports elapsed-time leases), and promising candidates may receive much
longer follow-up leases, up to about one hour when evidence justifies it.
`probe` uses short leases or turn budgets to establish feasibility, potential,
and blockers before the main agent decides whether to deepen or redirect.

This is prompt guidance, not a Goal/Search phase or runtime scheduler field.
The runtime removes the command prefix and appends one canonical exploration
line to `raw_goal`. Editing a goal preserves the current mode unless the edit
supplies another `mode=...` prefix.

## Rolling Search Loop

### 1. Plan and materialize

The main agent calls `search_plan_next`, then `search_start_batch`. A round is
the persisted plan created by that decision. It does not require all candidates
from the round to finish together.

`planned_k` is:

```text
min(requested_k, remaining max_candidates, max_parallel)
```

`max_candidates` caps distinct candidate workspaces for the whole run.
`max_parallel` caps live workers.

### 2. Fill free slots

For each selected candidate, the main agent creates a session with
`search_start_agent_session` and launches the returned host-native payload.
The worker must begin with `search_get_agent_context`; prompt ids are labels,
while returned context is authoritative.

The worker edits only its assigned workspace and records real iterations with:

```text
search_run_verifier(..., agent_session_id=<its session>)
```

Each worker returns a compact handoff: the important change, verifier-backed
results, blockers, next steps, and at most five scenario-specific pitfalls.

### 3. Wait for any completion

The host wait-any primitive wakes the main agent when at least one worker is
terminal. The main agent processes every new terminal event and runs a final
verifier without `agent_session_id` against that exact candidate state.

It then makes one evidence-based choice for the free slot:

| Choice | When |
|---|---|
| Continue the same worker | the direction is valuable and the host supports native continuation |
| Redispatch the same candidate | workspace/history are useful but a fresh worker is safer |
| Launch a new candidate | another distinct hypothesis is worth the remaining candidate budget |
| Leave idle | remaining time cannot support a useful attempt |
| Drain | current evidence is sufficient for selection |

The pool is refilled immediately. The main agent never waits for the slowest
member of an artificial batch before evaluating completed work.

### 4. Drain and select

Before selection, the main agent stops adding work and drains or closes every
live host worker. `search_select` ranks committed verifier iterations and
re-verifies exact commits. The first passing ranked commit becomes the selected
result. `search_report` writes evidence; `search_promote` exports a patch and
does not mutate the source workspace.

## Host Mapping

The control loop is shared; only the pool adapter changes.

| Operation | Codex | Pi |
|---|---|---|
| Launch | `spawn_agent` from runtime launch payload | `pi_search_pool_open` / `pi_search_pool_submit` |
| Wait any | targetless `wait_agent`, then `list_agents` | `pi_search_pool_wait_any` |
| Continue valuable work | `search_continue_agent_session`, then `followup_task` | `pi_search_pool_continue` state-level redispatch |
| Recover after interruption | live Codex agent registry plus `.gp` history | `pi_search_pool_snapshot(run_id=...)` plus `.gp` history |
| Close | drain/interrupt native agents | `pi_search_pool_close` |

See [Agent Host Adapters](agent-host-adapters.md) for all hosts.

## Resume Semantics

There are two distinct operations:

- **Same-worker continuation** keeps native conversational context. It is an
  optional host capability and may apply a larger one-dispatch budget.
- **State-level redispatch** creates a fresh `agent_session_id` in the same
  candidate workspace. It recovers from Git state, runtime iterations,
  history, and the structured handoff. This is the portable fallback.

Neither operation changes the frozen spec or creates another candidate.

## Top-level Stop

An active top-level Goal Plus turn never stops merely because its current next
action is optional or a worker lease ended. The Stop gate re-presents the full
current `raw_goal`, creation/check timestamps, elapsed time, phase, next action,
and final-check policy. The main agent audits every requirement, including any
time condition already written in the goal, then either continues or records a
truthful terminal status. Goal Plus stores no separate task deadline.

Candidate and ordinary-subagent stop rules are unchanged: a candidate may
return after its own verifier evidence is durable, while selection, promotion,
and the complete-goal audit remain parent-owned.

## Failure Rules

- Candidate errors do not invalidate passing earlier iterations. Selection can
  still use a committed historical best.
- `VerifierWorkspaceSideEffect` with
  `metrics.infrastructure_failure=true` and
  `metrics.candidate_action=stop_and_report` is an infrastructure failure.
  The worker stops; the parent repairs and refreezes.
- A frozen verifier or artifact hash mismatch invalidates the run result. Do
  not replace the current evaluator with an older frozen copy.
- Selection, reporting, promotion, and the final goal audit remain parent-owned
  even after a candidate worker finishes.
