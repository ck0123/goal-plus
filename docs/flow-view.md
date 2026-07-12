# Flow View: Who Does What, Who Sees What

This doc is the information-flow counterpart to [design.md](design.md). `design.md` describes the data model and state machine; this doc describes **which agent does which step, what each agent actually sees at runtime, and which OpenCode platform constraints gate the flow**.

The flow below describes one internal search task after `/goal-plus` has
created a goal record, recorded triage, and frozen or confirmed a verifier-backed
spec. A Goal Plus task may repeat this flow with another frozen spec/run. Use
this before designing strategy changes (evolve, mcts, hybrid). If a
planned feature depends on an agent seeing data that the platform or runtime
does not actually expose, the feature is dead on arrival.

## 1. Two Agents, One OpenCode Lifecycle

```text
┌─────────────────────────────────────────────────────────────────┐
│  Main Agent (goal-plus/search orchestrator, mode: primary)      │
│  Owns: plan batches + launch OpenCode Tasks + final verify      │
│  Sees: plan.work_orders, plan.official_history,                 │
│        launch payload (subagent_type, description, prompt),     │
│        run_verifier ScoreReport                                 │
└──────────────┬──────────────────────────────────────────────────┘
               │ The only worker-launch channel:
               │ OpenCode Task(subagent_type=<launch.subagent_type>,
               │               description=<launch.description>,
               │               prompt=<launch.prompt>)
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Subagent (AnySearchAgent[Flash|<none>|Deep|ExtraDeep])         │
│  mode: subagent, steps: 15 / 50 / 100 / 150                    │
│  Owns: one-candidate autoresearch loop (edit→verify→commit)    │
│  Sees: the dict returned by search_get_agent_context           │
└─────────────────────────────────────────────────────────────────┘
```

Three facts that repeatedly cause "API exists but agent can't use it" bugs:

1. **`search_start_agent_session` creates a context handle, not a worker.** It returns a launch payload (subagent_type, description, prompt). The actual worker is launched by the OpenCode `Task()` call that the main agent issues *in the same model turn*, using that launch payload. Without the Task call, no worker process runs. See `SKILL.md` Step 5 and `server.py` `search_start_agent_session` docstring.
2. **OpenCode `Task` has no `timeout` parameter.** There are no per-session or run-level time deadlines in this runtime. Subagents run until their OpenCode step cap hits or the user interrupts the run. There is no MCP abort tool.
3. **There is no MCP wait loop.** The main agent waits for each OpenCode Task to return, then binds metadata, verifies, and decides whether to continue the same session.

## 2. Single-Batch Information Flow

Each step lists **who acts** and **what they see**.

```text
[1] Main: search_freeze_spec(spec, verifier_artifact_paths)
    Main sees: frozen_spec_id
    Runtime hashes verifier files; later edits force score 0.0.

[2] Main: search_create(frozen_spec_id) → run_id
    Main sees: run_id

[2a] Main: goal_plus_link_search_run(goal_plus_id, frozen_spec_id, run_id)
    Runtime appends one search task to the Goal Plus record.
    linked_search remains the current-task compatibility view.

[3] Main: search_plan_next(run_id, requested_k=N)
    Main sees (the SearchPlan):
      • work_orders[] — per slot:
          base_candidate_id, parent_candidate_ids,
          inspiration_candidate_ids, intent, hypothesis,
          must_derive_from
      • official_history.candidates[] — top-N history payloads
        (full _history_candidate_payload: key_metrics,
        changed_files, score, failure_classes, verifiers, …)
      • proposal_contract (only agent_guided / external_mcp) —
        requires Main to supply proposals referencing official
        candidates
      • strategy_trace — parent_candidate_id, inspiration_ids,
        selection_rule, reason
      • worker_policy — subagent_type, mode,
        requires_agent_session

[4] Main: search_start_batch(run_id, plan_id, proposals?)
    Runtime copies workspace (from source or base_candidate).
    Main sees (per CandidateTask):
      candidate_id, workspace path, allowed_files, denied_files,
      instructions[], proposal, strategy_metadata
    Note: workspace is materialized but no worker is running yet.

[5] Main: search_start_agent_session(run_id, candidate_id, directive)
    Runtime writes AgentSessionRecord (context handle + launch payload).
    Main sees (the full record, including launch):
      agent_session_id, candidate_id, workspace, directive,
      launch.subagent_type, launch.description, launch.prompt
    Note: no worker is running yet — the record only describes what
    the worker will look like once Main launches Task.

[6] Main: Task(subagent_type=launch.subagent_type,
              description=launch.description,
              prompt=launch.prompt)
    OpenCode launches the subagent process.
    Main sees: Task result when the worker returns.
    Rule: the launch.prompt carries only agent_session_id + a
    human-readable candidate idea. The worker must derive
    run_id / candidate_id / workspace from MCP context, not from
    prompt-supplied labels.

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
      • iterations — this candidate's own run_verifier history
    The subagent treats this dict as the single source of truth.

[8] Subagent autoresearch loop (AnySearchAgent.md "Iteration Loop"):
    repeat until OpenCode step cap or self-decision:
      read context.iterations + context.history
      → pick next hypothesis
      → edit allowed_files
      → search_run_verifier(..., agent_session_id=self)
        (runtime auto-commits changed artifact files, appends an
         IterationRecord with git_head, returns ScoreReport)
      → keep exploring from the current or intentionally checked-out state
    finish:
      summarize the best verifier-recorded iteration in text.
      No finalize MCP call exists; OpenCode Task return is the
      lifecycle signal.

[9] Main waits for OpenCode Task return.
    There is no MCP wait loop.

[10] Main after Task return:
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
| `history.candidates[]` | Top-5 summaries: `key_metrics`, `changed_files` (names), `score` | Sees file names and prose, **not code diffs** |
| `iterations[]` | This candidate's own score trajectory | Own exploration memory |

**Core limitation:** a subagent **cannot read another candidate's code**. It sees file names and prose summaries only. To "mutate from parent's code," either (a) the parent's workspace was already copied into this candidate's workspace at `start_batch` (so the subagent reads its own workspace, which *is* the parent's snapshot), or (b) the main agent injects the parent's diff into `proposal.instructions`. The builtin `_plan_evolve` currently does (a) only — it copies the parent workspace but writes only a templated "mutate parent using inspirations" intent (runtime.py `_plan_evolve`).

## 4. Levers The Main Agent Actually Has

| Lever | How Main uses it | Current state |
|---|---|---|
| `plan_next(requested_k)` | Set generation size | Used |
| `proposal.instructions` | Inject concrete mutation direction into worker prompt | Builtin evolve writes a template; only `agent_guided` lets Main author it |
| `proposal.history_refs` | Point worker at specific inspirations | Set by evolve, but worker only gets ids, not content |
| `directive` (at session start or continuation) | Pass a goal to the worker | Mostly meaningful for `agent_guided`; in builtin evolve the proposal overrides it |
| `search_continue_agent_session(agent_session_id)` | Reuse the same OpenCode session and candidate workspace | Requires `search_bind_opencode_session` with the Task `metadata.sessionId`; this is not a fork |
| `search_redispatch_candidate(run_id, candidate_id)` | Start a new worker on the same candidate workspace | Portable state-level resume; may override `worker_agent_type` / `worker_budget` for the new launch |
| `budget.max_parallel` | Batch planning cap | Used to size planned groups, not to supervise Task lifecycle |

## 5. OpenCode Platform Hard Constraints

These are the root causes of "API exists but agent can't use it":

1. **Task has no `timeout`.** There are no per-session or run-level time deadlines. The worker runs until OpenCode's step cap (15/50/100/150) is hit or the user interrupts the run. Stopping a running subagent is an OpenCode/user concern.
2. **No MCP wait API.** The runtime records context/provenance and scores; it does not supervise Task lifecycle.
3. **Subagents have no direct communication.** Subagent A cannot read subagent B's workspace. There is no observation bus or peer-status channel. Cross-session learning must happen via Main's next `plan_next`, not at runtime.
4. **Subagent step budget is fixed per Task invocation.** 15/50/100/150, not dynamically adjustable inside a running invocation. A promising candidate can continue later through same-session continuation when Main bound the Task `metadata.sessionId`, or through `search_redispatch_candidate` when it needs a fresh worker or a larger tier.
5. **No MCP process cancellation.** Stopping a running subagent is an OpenCode/user interruption concern, not an MCP call. There is no MCP abort tool.

## 6. Diagnosis: What Evolve Needs That Is Currently Missing

Stacking the flow view against `_plan_evolve` / `_plan_mcts`, four break points explain why evolution does not actually evolve:

**Break A — Worker cannot see parent/inspiration code.**
`_plan_evolve` selects a parent and inspiration_ids, but `get_agent_context` only surfaces ids and prose summaries, not diffs. The worker's "mutation" is effectively memoryless re-exploration, not true inheritance.
Root cause: OpenCode platform (subagents can't read peers' workspaces) + runtime not injecting diffs into `proposal.instructions`.

**Break B — `parent_policy` is a dead field.**
`StrategySpec.parent_policy: dict` is defined in `models.py` but has zero references in `runtime.py`. There is no way to configure tournament size, elitism, multi-parent crossover, or any selection policy without editing builtin planner code.

**Break C — Subagent step budget is per invocation (platform limit).**
A candidate that shows promise at step 50 cannot have the running invocation's cap edited in place. It can be reinvested in by continuing the same bound OpenCode session with `search_continue_agent_session`, or by calling `search_redispatch_candidate` to create a new `agent_session_id` for the same candidate workspace with a larger tier/budget.

**Priority for unblocking evolve: A > B > C.**
- A is the root information bottleneck. Fix: `_plan_evolve` should embed the parent's key diff / changed-file content into `proposal.instructions` so the worker truly stands on the parent's shoulders.
- B: wire `parent_policy` into `_plan_evolve` to support tournament / elitism / multi-parent configuration.
- C: supported as same-session continuation after binding the OpenCode session id, or as state-level resume through `search_redispatch_candidate`; neither path is a fork or a dynamic in-process step-cap change.

## 7. Removed APIs

The supervisor model was eliminated. The runtime no longer exposes lifecycle control, observation, host-sync, or batch-shortcut tools. Concretely: no MCP wait, status, abort, finalize, submit, observation, host-sync, or batch-shortcut APIs exist. The CLI exposes only `--root`.

Do not reintroduce them. If you find yourself wanting lifecycle control in MCP, that is a signal that the design has regressed; the lifecycle belongs to OpenCode.
