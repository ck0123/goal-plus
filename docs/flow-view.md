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
                 -> verifier concern? audit evidence
                      -> unconfirmed: keep current run
                      -> confirmed: invalidate -> stop all workers
                           -> repair/freeze -> successor run
            -> search_select -> search_report -> search_promote
            -> goal_plus_record_search_result
  -> audit the full current goal revision
  -> optional independent final check
  -> terminal goal status
```

A Goal Plus record may contain multiple search tasks. Each task is one
`run_id` over one frozen spec. Keep one run across asynchronous planning
decisions while its evaluation/edit contract is valid and its immutable budget
is usable. A successor run exists only for a revised contract/subproblem or an
exhausted immutable budget; Search tasks are not nested.

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
search_run_verifier(
  ...,
  agent_session_id=<its session>,
  hypothesis=<concise design tested>,
)
```

Every returned verifier report appends exactly one durable result-ledger entry
to `workspace/results.tsv` and commits that file. Before appending, the runtime
checks that the existing file is byte-for-byte equal to its durable ledger and
Git-clean; worker edits raise `ResultsLedgerMutation`. Child candidates inherit
the base candidate's ledger; successor runs inherit the selected/best source
candidate's ledger. A call that raises before producing a report adds no row.
The file is runtime metadata and is excluded from edit-surface and promotion
diffs. Workers inspect this continuous design history but never edit it.

Each worker returns a compact `.tmp/handoff.json`. The runtime projects it into
candidate history and also builds a bounded, current-run rollup across all
candidates, so a useful feature does not disappear merely because its candidate
falls outside the visible score frontier.

| Worker field | Main-agent use |
|---|---|
| `summary` | one-line state of the candidate |
| `key_results` | feature ledger: code surface/change, artifact or Git head, portability/dependencies, measured effect, verifier result, and relation to incumbent |
| `pitfalls` | conditional observations with scope, condition, evidence artifact, confidence, and recommendation |
| `blockers` | constraints that prevent useful continuation |
| `next_steps` | concrete follow-ups, including possible feature transfers |
| `verifier_assessment` | evidence-backed `adequate`, `concern`, or `unknown`; sparse diagnostics and low scores are not concerns |

This extends the prior handoff rather than adding another protocol:

| Before | Now |
|---|---|
| `key_results`: artifact/change/result/conclusion | same entries plus code surface, portability, dependencies, measured effect, and incumbent relation |
| no explicit evaluator-quality signal | bounded `verifier_assessment` with evidence and recommended action |
| top candidate summaries only drove planning | run-level ledger also retains non-winning portable innovations |

Pitfalls are deliberately more conservative than features:

| Pitfall scope | Propagation rule |
|---|---|
| `candidate_local` | only the same candidate's continuation or redispatch |
| `feature_family` | only a target with the same mechanism and matching condition |
| `evaluation_contract` | only after the main agent independently confirms it |

Missing/unknown scope becomes `candidate_local`; missing confidence becomes
`single_observation`. One worker failure never becomes a global search ban.

### 3. Wait for any completion

The host wait-any primitive wakes the main agent when at least one worker is
terminal. The main agent processes every new terminal event and runs a final
verifier without `agent_session_id` against that exact candidate state.

It then considers three search actions without fixed counts:

| Search action | Meaning |
|---|---|
| `deepen_incumbent` | continue a strong artifact or candidate |
| `transfer_feature` | probe a portable, orthogonal feature from another candidate against the incumbent |
| `macro_restart` | begin a structurally different direction from source or an earlier ancestor |

The chosen action is recorded in `proposal.metadata.search_action`. The main
agent then makes one evidence-based pool choice for the free slot:

| Choice | When |
|---|---|
| Continue the same worker | the direction is valuable and the host supports native continuation |
| Redispatch the same candidate | workspace/history are useful but a fresh worker is safer |
| Launch a new candidate | another distinct hypothesis is worth the remaining candidate budget |
| Leave idle | remaining time cannot support a useful attempt |
| Drain | current evidence is sufficient for selection |

The pool is normally refilled immediately. The only exception is an active
verifier concern audit: the main agent pauses refill while checking concrete
evidence, but leaves existing workers alone until the concern is confirmed.
The main agent never waits for the slowest member of an artificial batch before
evaluating ordinary completed work.

`candidate_ready` is a decision event, not run completion. A new incumbent does
not require select/promote/new-run checkpointing because verifier-recorded Git
iterations are already durable. Keep one run while its evaluation/edit contract
is adequate and immutable candidate budget remains. If a contract or measurable
subproblem revision (or exhausted run budget) makes another run unavoidable,
the main agent calls `search_create(..., source_run_id=<old run>)`. The runtime
snapshots the old frontier, scoped pitfalls, feature ledger, and non-winning
portable innovations into `inherited_research`. Old scores are explicitly
non-reusable and every imported feature must be verified again.

### 4. Confirmed verifier invalidation

A worker can report `verifier_assessment.status=concern`; only the main agent
can confirm it. Low scores, sparse diagnostics, and slow progress are not
contract failures. Confirmation requires evidence such as valid/invalid
misclassification, missing raw-goal coverage, nondeterminism, hash/contract
drift, target mismatch, or verifier infrastructure failure.

Once confirmed, ordering is mandatory:

1. Call `search_invalidate_run` with a typed reason, summary, and concrete
   evidence. This atomically fences planning, new sessions, verifier-result
   recording, selection, and promotion.
2. Stop every live host worker and wait until the host reports zero active
   workers. A verifier already in flight cannot write into the invalidated run.
3. Preserve terminal handoffs and workspaces; never select or promote the old
   run.
4. Repair or regenerate the source-owned verifier only after workers stop;
   freeze a new immutable spec.
5. Create the successor with `source_run_id`, link it to the same Goal Plus
   record, and re-verify any inherited artifact or feature.

### 5. Drain and select

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
| Confirmed verifier invalidation | `search_invalidate_run`, interrupt every live agent, wait until terminal | `search_invalidate_run`, `pi_search_pool_close(mode="interrupt")`, wait for `active_count=0` |
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
- A worker verifier `concern` is advisory evidence for the main agent. Refreeze
  only for demonstrated contract misalignment, missing raw-goal coverage,
  nondeterminism, or local/target mismatch; difficulty optimizing is not enough.
- Once confirmed, invalidate before interrupting workers. The invalidated run is
  immutable fault evidence: no new score, selection, or promotion may enter it.
- A frozen verifier or artifact hash mismatch invalidates the run result. Do
  not replace the current evaluator with an older frozen copy.
- Selection, reporting, promotion, and the final goal audit remain parent-owned
  even after a candidate worker finishes.
