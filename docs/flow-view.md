# Flow View: Who Does What, Who Sees What

This doc is the information-flow counterpart to [design.md](design.md). `design.md` describes the data model and state machine; this doc describes **which agent does which step, what each agent actually sees at runtime, and which OpenCode platform constraints gate the flow**.

Use this before designing strategy changes (evolve, mcts, hybrid). If a planned feature depends on an agent seeing data that the platform or runtime does not actually expose, the feature is dead on arrival.

## 1. Two Agents, One Control Plane

```text
┌─────────────────────────────────────────────────────────────────┐
│  Main Agent (search-orchestrator, mode: primary)                │
│  Owns: dispatch + supervisor loop + final verify/select        │
│  Sees: plan.work_orders, plan.official_history, observations,  │
│        wait_agent_events result, run_verifier ScoreReport      │
└──────────────┬──────────────────────────────────────────────────┘
               │ The only worker-launch channel:
               │ OpenCode Task(subagent_type=AnySearchAgent,
               │               background=true)
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Subagent (AnySearchAgent[Flash|<none>|Deep|ExtraDeep])         │
│  mode: subagent, steps: 15 / 50 / 100 / 150                    │
│  Owns: one-candidate autoresearch loop (edit→verify→commit)    │
│  Sees: the dict returned by search_get_agent_context           │
└─────────────────────────────────────────────────────────────────┘
```

Three facts that repeatedly cause "API exists but agent can't use it" bugs:

1. **`search_start_agent_session` does not start a worker.** It only writes an MCP-side `AgentSessionRecord` (runtime.py `start_agent_session`). The actual worker process is launched by the OpenCode `Task()` call that the main agent issues *in the same model turn*. Without that `Task` call, the session sits idle and `search_wait_agent_events` returns with `poll_window_expired=True` and no real work done. See `SKILL.md` Step 5 and `server.py` `search_start_agent_session` docstring.
2. **OpenCode `Task` has no `timeout` parameter.** There are no per-session or run-level time deadlines in the runtime. Subagents run until their OpenCode step cap hits or the supervisor aborts them. See `SKILL.md` "OpenCode Host Requirement".
3. **`max_parallel > 1` requires `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true` on the OpenCode process** (not just in `opencode.json`'s MCP env). Without it, `background: true` does not return control and the supervisor loop is dead. See `opencode.md` "Start".

## 2. Single-Batch Information Flow

Each step lists **who acts** and **what they see**.

```text
[1] Main: search_freeze_spec(spec, verifier_artifact_paths)
    Main sees: frozen_spec_id
    Runtime hashes verifier files; later edits force score 0.0.

[2] Main: search_create(frozen_spec_id) → run_id
    Main sees: run_id

[3] Main: search_plan_next(run_id, requested_k=N)
    Main sees (the SearchPlan):
      • work_orders[] — per slot:
          base_candidate_id, parent_candidate_ids,
          inspiration_candidate_ids, intent, hypothesis,
          must_derive_from
      • official_history.candidates[] — top-N history payloads
        (full _history_candidate_payload: summary, key_metrics,
        changed_files, score, failure_classes, verifiers, …)
      • proposal_contract (only agent_guided / external_mcp) —
        requires Main to supply proposals referencing official
        candidates
      • strategy_trace — parent_candidate_id, inspiration_ids,
        selection_rule, reason
      • worker_policy — subagent_type, timeout_seconds, mode,
        requires_agent_session, supervisor_tools

[4] Main: search_start_batch(run_id, plan_id, proposals?)
    Runtime copies workspace (from source or base_candidate).
    Main sees (per CandidateTask):
      candidate_id, workspace path, allowed_files, denied_files,
      instructions[], proposal, strategy_metadata
    Note: workspace is materialized but no worker is running yet.

[5] Main: search_start_agent_session(run_id, candidate_id, directive)
    Runtime writes AgentSessionRecord (deadline, heartbeat, binding).
    Main sees: agent_session_id
    Note: session.status == running but no worker process exists yet.

[6] Main: Task(subagent_type=AnySearchAgent, background=true,
              prompt="agent_session_id=<id>; <one-paragraph idea>")
    OpenCode launches the subagent process.
    Main sees: Task handle (returns immediately in background mode)
    Rule: prompt must contain only agent_session_id + human-readable
    idea. Never hard-code run_id / candidate_id / workspace paths.
    The worker derives identifiers from MCP context only.

[7] Subagent first action:
    search-runtime_search_get_agent_context(agent_session_id)
    Subagent sees (the full context dict, runtime.py get_agent_context):
      • agent_session_id, run_id, candidate_id, workspace
      • objective, metric_name, metric_direction
      • candidate_task — allowed_files, denied_files, instructions,
        proposal (intent, expected_tradeoff, history_refs,
        parent_candidate_ids, base_candidate_id, hypothesis,
        strategy_metadata)
      • history — list_history(top_n=5, sort_by="score"): top-5
        candidates across the run (summary + key_metrics +
        changed_files + score; NO code diffs)
      • peer_status — other sessions' status/phase/summary
        (no workspace content)
      • observations — list_observations(top_n=20): cross-session
        findings that peers chose to publish
      • iterations — this candidate's own run_verifier history
    The subagent treats this dict as the single source of truth.

[8] Subagent autoresearch loop (AnySearchAgent.md "Iteration Loop"):
    repeat until steps / time / deadline exhausted:
      read context.iterations + context.history + context.observations
      → pick next hypothesis
      → edit allowed_files
      → search_run_verifier(..., agent_session_id=self)
        (runtime appends an IterationRecord, returns ScoreReport)
      → if improved: git commit; else: git reset --hard HEAD~1
      → (optional) search_publish_observation(summary, evidence,
        next_ideas, tags)
    finish:
      search_submit_candidate(artifact with agent_session_id)
      search_finish_agent_session(status, summary, result)

[9] Main: search_wait_agent_events(run_id, timeout_seconds,
                                   since_event_id=last_event_id)
    Runtime blocks polling until a terminal event or poll timeout.
    Main sees:
      • events[] — agent_completed/failed/blocked/aborted
      • sessions[] — all session current state
      • active_count, max_concurrent_agents
      • last_event_id — pass into next wait for incremental events
    Note: while blocked here, Main cannot plan/start the next batch.
    The architecture is batch-synchronous, not streaming.

[10] Main on terminal event:
     search_run_verifier(run_id, candidate_id, scope="process")
       (without agent_session_id — confirms final score against the
        best-so-far workspace state)
     Main sees: ScoreReport (aggregate_score, failure_class,
     key_metrics, verifier_results)

[11] Loop back to [3]: if budget remains and Main wants to evolve,
     call plan_next again. In evolve mode the runtime reads all
     scored candidates, picks best as parent, top-N as inspirations.
```

## 3. What The Subagent Can Actually See (Evolve Bottleneck)

`get_agent_context` is the only channel through which a subagent learns about the rest of the run. The table maps each field to its value for evolution-style strategies.

| Field | Content | Value for evolve |
|---|---|---|
| `candidate_task.parent_candidate_ids` | Who this candidate mutated from | Knows the parent id |
| `candidate_task.base_candidate_id` | Whose workspace was copied | Knows the baseline id |
| `candidate_task.proposal.intent` | This batch's mutation intent | Knows what to explore |
| `candidate_task.proposal.history_refs` | Inspiration ids | Knows who to borrow from — by id only |
| `history.candidates[]` | Top-5 summaries: `summary` text, `key_metrics`, `changed_files` (names), `score` | Sees file names and prose, **not code diffs** |
| `observations[]` | What peers chose to publish (`next_ideas`, `evidence`) | Only as rich as peers volunteer |
| `iterations[]` | This candidate's own score trajectory | Own exploration memory |
| `peer_status[]` | Other sessions' phase + one-line summary | Too coarse to act on |

**Core limitation:** a subagent **cannot read another candidate's code**. It sees file names and prose summaries only. To "mutate from parent's code," either (a) the parent's workspace was already copied into this candidate's workspace at `start_batch` (so the subagent reads its own workspace, which *is* the parent's snapshot), or (b) the main agent injects the parent's diff into `proposal.instructions`. The builtin `_plan_evolve` currently does (a) only — it copies the parent workspace but writes only a templated "mutate parent using inspirations" intent (runtime.py `_plan_evolve`).

## 4. Levers The Main Agent Actually Has

| Lever | How Main uses it | Current state |
|---|---|---|
| `plan_next(requested_k)` | Set generation size | Used |
| `proposal.instructions` | Inject concrete mutation direction into worker prompt | Builtin evolve writes a template; only `agent_guided` lets Main author it |
| `proposal.history_refs` | Point worker at specific inspirations | Set by evolve, but worker only gets ids, not content |
| `directive` (at session start) | Pass a goal to the worker | Mostly meaningful for `agent_guided`; in builtin evolve the proposal overrides it |
| Observations reading | Read obs after a batch to inform the next plan | `_plan_evolve` does **not** read observations (acknowledged in `docs/superpowers/plans/2026-06-26-autoresearch-subagent.md`) |
| `budget.max_parallel` | Concurrency | Used |

## 5. OpenCode Platform Hard Constraints

These are the root causes of "API exists but agent can't use it":

1. **Task has no `timeout`.** There are no per-session or run-level time deadlines. The worker runs until OpenCode's step cap (15/50/100/150) is hit or Main aborts it.
2. **Background Task requires an env flag.** Without `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true` on the OpenCode process, `background: true` does not return control. Main blocks on a foreground Task and the supervisor loop is dead.
3. **Subagents have no direct communication.** Subagent A cannot read subagent B's workspace. The only channel is `publish_observation` → Main reads it → Main injects into the next plan's `proposal.instructions`. **This is the single biggest information bottleneck for evolve.**
4. **Subagent step budget is fixed per variant.** 15/50/100/150, not dynamically adjustable. "Give a promising candidate more steps" is not expressible; you can only relaunch a new session with a deeper variant.
5. **`search_abort_agent_session` is MCP state, not process cancellation.** The runtime marks the session aborted, but whether the OpenCode child session actually stops depends on host adapter wiring that is not currently connected. See `design.md` "Current Boundary" and `debugging-runtime.md` "Session status is 'aborted' but workspace files keep changing".
6. **Main is blocked while inside `wait_agent_events`.** The implementation is a `while True` + `time.sleep(0.1)` poll (runtime.py `wait_agent_events`). Main cannot "wait and plan in parallel" — it must return from wait before issuing the next `plan_next`. Streaming evolution (plan gen N+1 while gen N runs) is not supported by the current architecture.

## 6. Diagnosis: What Evolve Needs That Is Currently Missing

Stacking the flow view against `_plan_evolve` / `_plan_mcts`, four break points explain why evolution does not actually evolve:

**Break A — Worker cannot see parent/inspiration code.**
`_plan_evolve` selects a parent and inspiration_ids, but `get_agent_context` only surfaces ids and prose summaries, not diffs. The worker's "mutation" is effectively memoryless re-exploration, not true inheritance.
Root cause: OpenCode platform (subagents can't read peers' workspaces) + runtime not injecting diffs into `proposal.instructions`.

**Break B — Observations do not reach the planner.**
Workers publish `next_ideas` into observations. `_plan_evolve` does not read them (acknowledged in the autoresearch-subagent plan doc). Cross-session discoveries never influence parent selection or mutation direction. Evolve degrades to "pick best, re-mutate" each generation.

**Break C — `parent_policy` is a dead field.**
`StrategySpec.parent_policy: dict` is defined in `models.py` but has zero references in `runtime.py`. There is no way to configure tournament size, elitism, multi-parent crossover, or any selection policy without editing builtin planner code.

**Break D — Subagent step budget is not dynamic (platform limit).**
A candidate that shows promise at step 50 cannot be given 50 more; you can only relaunch a new session with `AnySearchAgentDeep` (100) or `ExtraDeep` (150). "Reinvest in promising branches" is not expressible in the current budget model.

**Priority for unblocking evolve: A > B > C > D.**
- A is the root information bottleneck. Fix: `_plan_evolve` should embed the parent's key diff / changed-file content into `proposal.instructions` so the worker truly stands on the parent's shoulders.
- B: make `_plan_evolve` read `list_observations` and surface worker `next_ideas` as the mutation candidate pool for the next generation.
- C: wire `parent_policy` into `_plan_evolve` to support tournament / elitism / multi-parent configuration.
- D: platform limit, not directly fixable; compensate at the budget layer by relaunching promising candidates with a deeper variant.
