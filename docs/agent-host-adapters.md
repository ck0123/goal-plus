# Agent Host Adapters

Adapters translate runtime launch/continue requests into host-native worker
operations. The Search runtime stays unchanged; [Flow](flow-view.md) defines the
shared loop.

## Common Contract

`src/goal_plus/agent_pool.py` defines `HostPoolContract` and terminal
`WorkerPoolEvent` values. Each host declares:

| Field | Contract question |
|---|---|
| `launch_mode` | does launch return immediately? |
| `wait_mode` | can the parent wake on any completion? |
| `continuation_mode` | same worker or fresh state redispatch? |
| `deadline_mode` | which host component enforces runtime? |
| `recovery_mode` | how is a live pool rediscovered? |
| `completion_stage` | when is the candidate safe for parent evaluation? |

The adapter also returns authoritative launch fields. The main agent projects
only fields supported by the current host tool schema; for Codex, that means the
current `spawn_agent` schema rather than assumed optional metadata.

## Capability Matrix

| Capability | OpenCode | Codex | Claude Code | Pi RPC |
|---|---|---|---|---|
| Launch | foreground `Task` | async `spawn_agent` | foreground `Agent` | detached local supervisor + foreground Pi child |
| Wait mode | Task return | `wait_agent` any-event wake + `list_agents` | Agent return | `pi_search_pool_wait_any` |
| Continuation | same Task via `task_id` | same worker via `followup_task` | conditional host support | same native session in a new process |
| Deadline | step-tiered agent | per-dispatch parent watchdog | `maxTurns` agent | Pi process watchdog |
| Recovery | Task handle + `.gp` | native agent registry + `.gp` | handle when exposed + `.gp` | persisted `.gp/host-pools/pi/` + `.gp` |
| Goal gate | instruction-driven | `UserPromptSubmit`, `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop` | PostToolUse binding + Stop backstop | extension input/tool/turn events |
| Strategy coverage | all existing tested paths | portable builtins | portable builtins | portable builtins |
| Trace export | yes | no | no | no |
| Normalized observability | bound metadata | native session JSONL + bound metadata | bound metadata | `pi_metrics` + bound metadata |

All adapters implement the read-only `collect_observability` contract exposed
as `search_get_agent_observability`. This is provenance and diagnostics only;
it does not add worker lifecycle state to Search records or turn the runtime
into a supervisor.

Portable builtins are `agent_guided`/`agent`/`default` and
`random`/`random_mode`. Other strategies remain OpenCode-only until a host has
contract tests and a real parallel-loop smoke.

## Parallel Loops

Codex and Pi both satisfy asynchronous wait-any semantics:

- **Codex** launches the initial candidate set once, waits for any mailbox
  update, then uses `list_agents` to discover all newly terminal workers. After
  parent completion verification it continues that same worker through
  `search_continue_agent_session` plus `followup_task` unless a global stop
  condition is true.
- **Pi** persists pool/job state, returns candidate-ready only after the full
  Pi driver chain and final verification, and never auto-refills. After each
  terminal event main calls `continue` for that same candidate unless a global
  stop condition is true. Pi reloads the same native session in a new process.

New Pi/Codex specs set `orchestration_mode="parallel_loops"`; one initial round
creates the durable candidate loops. Neither adapter turns that round into a
completion barrier. Low score or no improvement never causes replacement.

## Worker Budgets

| Host | Required control | Enforcement |
|---|---|---|
| OpenCode | worker tier | fixed host step cap |
| Codex | `worker_budget.max_runtime_seconds` | initial wait, one closeout message, final wait, interrupt |
| Claude Code | `worker_budget.max_turns` | selected agent's `maxTurns` |
| Pi RPC | `worker_budget.max_runtime_seconds` | closeout steer plus hard process watchdog |

`max_turns` is only a prompt hint for Codex and Pi. `max_candidates` limits
distinct candidate workspaces; `max_parallel` limits live workers. None of
these is a forced round count.

Codex additionally supports a lower-bound single-worker AutoResearch lease
through `worker_budget.min_runtime_seconds` and `min_verifier_runs`. Its
`SubagentStop` hook continues the same child turn until the lower bound is
satisfied, while `max_runtime_seconds` remains the independent parent-watchdog
upper bound. The adapter requires the lease to release before the parent soft
closeout, preventing the two controls from racing. This lower-bound lease is
currently Codex-only.

`strategy.worker_launch` carries optional host launch preferences. Codex maps
`model`, `reasoning_effort`, and `service_tier` when exposed; Pi maps model and
thinking level through trusted process configuration. These values do not
belong to Search state.

## Resume And Handoff

State-level redispatch is the portable recovery path:

1. call `search_redispatch_candidate` for an existing candidate;
2. launch the fresh `agent_session_id` in the same workspace;
3. the worker reloads `search_get_agent_context`;
4. Git state, verifier iterations, ranked history, and `research_summary`
   replace dependence on a previous transcript.

Same-worker continuation is native on Codex and OpenCode. Pi provides native
session continuation across process boundaries: each dispatch has a new PID,
but retains the same native session, runtime `agent_session_id`, candidate, and
workspace. State-level redispatch remains the portable fallback for hosts or
legacy records without a resumable native session.

Every worker handoff should state the most important work, verifier-backed
feature entries, blockers, next steps, and at most five scoped conditional
pitfalls. Candidate-local pitfalls stay local; feature-family pitfalls transfer
only when mechanism and conditions match. Verifier concerns remain advisory
until the main agent confirms them.

## Confirmed Verifier Invalidation

The runtime fence is host-neutral; quiescence is adapter-specific:

| Step | Codex | Pi RPC |
|---|---|---|
| Fence | `search_invalidate_run` | `search_invalidate_run` |
| Stop live work | `interrupt_agent` for every live candidate | `pi_search_pool_close(mode="interrupt")` |
| Prove quiescence | `list_agents`/`wait_agent` until all terminal | snapshot/wait until `active_count=0` |
| Rebuild | repair/freeze only after quiescence | repair/freeze only after quiescence |
| Successor | `search_create(..., source_run_id=old)` | same |

Adapters must not attempt to refill an invalidated run. The runtime also rejects
Pi pool open/submit and rejects a verifier result that finishes after the fence.
The old run remains readable for diagnosis and research inheritance, but its
scores cannot be promoted or reused by the successor.

## Verification Evidence

| Path | Repository evidence |
|---|---|
| Codex parallel-loop cycle | `codex_parallel_loop_cycle`: two initial candidates, one plan, same native worker continuation, best update, final selection/report |
| Pi managed pool | `pi_rpc_managed_pool_wait_any`: two detached real Pi workers, pool rediscovery, candidate-ready events, drain |
| Pi parallel-loop cycle | `pi_rpc_parallel_loop_cycle`: one initial plan, same-candidate redispatch with a new session, best update, final selection/report |
| OpenCode | broad existing unit/assets and opt-in system scenarios |

Fast tests prove schemas and adapter mappings. Only the opt-in real-host tests
prove native launch, waiting, continuation, hooks, and provider behavior.

## Adapter Responsibilities

An adapter may:

- build launch and continuation payloads;
- validate host-specific budget fields;
- declare pool capabilities;
- preserve native handles and bounded host metadata.

It must not create candidate workspaces, execute/rank verifiers, plan the next
hypothesis, generate reports, or export promotion patches.

To add a host, register an adapter in `src/goal_plus/agent_hosts.py`, add local
assets and docs, cover launch/bind/budget/continuation in unit tests, and add a
real multi-round smoke before claiming end-to-end support.
