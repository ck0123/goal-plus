# API

`goal-plus --root .gp` exposes the host-neutral MCP surface. Tool schemas and
descriptions from the running server are authoritative; this page is the short
index and ownership guide.

## Goal Plus Tools

| Tool | Purpose |
|---|---|
| `goal_plus_create` | create a durable goal before triage |
| `goal_plus_status` | read goal phase, revision, linked tasks, and evidence |
| `goal_plus_update_goal` | replace the complete effective objective and start a revision |
| `goal_plus_record_triage` | choose ordinary goal work or verifier/spec discovery |
| `goal_plus_save_spec_draft` | persist the typed candidate Search spec |
| `goal_plus_confirm_frozen_verifier` | record optional verifier-approval evidence |
| `goal_plus_link_search_run` | append a frozen Search run to the goal |
| `goal_plus_record_search_result` | attach selected/promotion evidence and reserve canonical final report paths |
| `goal_plus_prepare_final_check` | create a required independent-review request |
| `goal_plus_submit_final_check` | record reviewer verdict for an exact revision |
| `goal_plus_set_status` | set evidence-backed terminal or paused state |
| `goal_plus_gate` | return a hook-friendly allow/block decision |

`goal_plus_update_goal` requires `expected_revision`, preventing a stale agent
from overwriting a newer objective. Search results are keyed by `run_id`, so one
goal can retain multiple search tasks.

## Search Tools

### Spec and run

| Tool | Purpose |
|---|---|
| `search_freeze_spec` | preflight and hash-pin a `SearchSpec` plus verifier artifacts |
| `search_create` | create a `run_id`; optional `source_run_id` snapshots bounded predecessor research with non-reusable scores |
| `search_status` | read budget use, candidates, and current best |
| `search_invalidate_run` | atomically fence a run after main-confirmed verifier inadequacy |
| `search_list_history` | rank candidates and return current-run feature/verifier research rollups |
| `search_list_iterations` | inspect every verifier iteration for one candidate |
| `goal_plus_monitor_snapshot` | read combined goal/run/session/host evidence without controlling workers |

### Plan and materialize

| Tool | Purpose |
|---|---|
| `search_plan_next` | persist one planning round |
| `search_start_batch` | materialize that plan's isolated candidate workspaces |

`strategy.orchestration_mode` is `rolling_candidates` for backward
compatibility or `parallel_loops` for the new Pi/Codex flow. In
`parallel_loops`, `search_plan_next(requested_k)` may be called exactly once;
later work uses continuation/redispatch of existing candidates. It plans:

```text
min(requested_k, remaining max_candidates, max_parallel)
```

The default `requested_k=4` is a request for one planning call, not a whole-run
budget. `max_candidates` is the immutable cap on distinct workspaces across all
rounds.

`search_invalidate_run` requires a typed verifier reason, non-empty summary,
and concrete evidence. It changes the run to `aborted` and blocks new planning,
sessions, verifier records, selection, and promotion. It does not own host
workers: the caller must next interrupt the complete host pool and wait for zero
active workers before repairing verifier files.

When a successor is unavoidable, use:

```text
search_create(new_frozen_spec_id, source_run_id=invalidated_or_exhausted_run)
```

The new run exposes `inherited_research` containing a predecessor frontier,
feature ledger, and scoped pitfalls. It marks predecessor scores non-reusable.
`strategy.history_policy.inherited_feature_limit` and
`inherited_pitfall_limit` bound the inherited ledgers by default; set either to
`null` to disable that runtime truncation when the host context can carry the
full history.

### Worker context

| Tool | Caller | Purpose |
|---|---|---|
| `search_start_agent_session` | main | create a provenance handle and host-native launch payload |
| `search_redispatch_candidate` | main | create a fresh session in the same candidate workspace |
| `search_bind_agent_handle` | main/host driver | attach a Codex, Claude, or Pi native handle |
| `search_bind_opencode_session` | OpenCode main | attach a Task session id |
| `search_continue_agent_session` | main | return native same-worker continuation fields when supported |
| `search_get_agent_context` | candidate worker | load authoritative ids, workspace, history, iterations, and resume data |
| `search_get_agent_observability` | main/monitor | read normalized model, timing, terminal, usage, context, artifact, and handoff evidence for one session |

`search_start_agent_session` does not launch or supervise a worker. The caller
must use the returned `launch` object. A one-dispatch `worker_budget` can be
passed to initial launch, continuation, or redispatch without mutating the
frozen spec.

`search_get_agent_observability` has one versioned cross-host schema. Schema
version 2 adds `execution.provider` and `usage.processed_tokens`; Pi processed
tokens include input, cache read/write, and output tokens, while Codex uses its
native total-token counter because cached input is already included. Codex
reads its native subagent session JSONL (bound by `SubagentStop` or discovered
from the unique task name); Pi normalizes `metadata.pi_metrics`. OpenCode and
Claude Code expose the portable evidence already bound to their handles. The
call never returns prompt, reasoning, tool arguments, or tool output content,
and never waits for or controls a worker. `goal_plus_monitor_snapshot` embeds
the same object under each `subagents[].observability` while retaining legacy
Pi fields for backward compatibility.

`goal_plus_monitor_snapshot.statistics` is the unified statistical view. Its
selected-run payload reports baseline/target improvement, success rates,
stable terminal duration, time to first verifier/success, worker outcome and
model/provider distributions, candidate lineage, selection survival,
worker-vs-parent verifier counts, promotion report evidence, normalized usage,
efficiency, and data-completeness gaps.
When a Codex Goal Plus transcript is bound, `statistics.orchestrator` reports a
content-free usage delta since Goal Plus creation, and `statistics.total_usage`
combines that delta with worker usage. Per-task statistics are also retained in
`search_tasks[].statistics` and aggregated under
`search_task_aggregate.statistics`.

Worker handoffs remain one bounded protocol. `key_results` supplies feature
ledger entries (artifact, code surface/change, portability/dependencies,
measured effect, verifier result, and incumbent relation), while
`verifier_assessment` reports evidence-backed contract quality. Candidate
history preserves these fields, and top-level `feature_ledger` and
`verifier_assessments` aggregate the current run across candidates outside the
visible ranking frontier as well as those inside it.

Pitfalls are not a run-wide deny list. Their `scope` is `candidate_local`,
`feature_family`, or `evaluation_contract`, with `condition`, evidence artifact,
and `confidence`. Missing scope defaults to candidate-local. A worker's
`verifier_assessment` is advisory until the main agent confirms it and calls
`search_invalidate_run`.

### Verify and finish

| Tool | Purpose |
|---|---|
| `search_run_verifier` | record a worker iteration, validate the existing inherited `workspace/results.tsv`, append exactly one row, and commit the ledger; workers pass `agent_session_id` plus a concise `hypothesis`, while parent final verification omits the session id |
| `search_select` | restore ranked commits and select the first final-verifier passing state |
| `search_report` | generate final `report.md` and self-contained `report.html`; linked Goal Plus records must already be terminal |
| `search_promote` | export the selected commit as a patch; normal Goal Plus flow has no report to refresh yet |

`report.html` is the complete Goal Plus audit view for the run passed to
`search_report`. When that run belongs to a Goal Plus record, the page keeps
every linked Search task separate and then provides a cross-task aggregate.
Planning-round counts remain in normalized data but do not have a separate
report panel. The report includes unified statistics,
candidate/session/verifier evidence, normalized main-agent usage, explicit
metric gaps, and one independent execution timeline for each Search task. The
Goal Plus state is summarized at the top rather than repeated in a separate
lifecycle panel. Each Search timeline is assembled from run creation,
worker-session observability, verifier iterations, and promotion evidence.
Worker bars use observed host start/end timestamps. Configured maximum or
minimum budgets are not rendered as actual duration. The file has inline
CSS/JavaScript only and is readable without a web server. `report.md` remains
the stable text artifact. A recorded Goal Plus Search result reserves both
canonical paths before the files exist. Normal Goal Plus order is select,
promote, record result, final audit, terminal status, then one report generation
per recorded run. Intermediate Goal Plus reports are rejected.

## Pi Local Tools

Pi's extension uses `goal-plus-pi-tool`, a JSON CLI facade over the same Python
runtime. These pool tools are host-local and are not added to the shared MCP
server:

| Tool | Purpose |
|---|---|
| `pi_search_pool_open` | create/recover a pool and optionally launch initial candidates |
| `pi_search_pool_submit` | launch one candidate into a free slot |
| `pi_search_pool_wait_any` | return new terminal candidate-ready events |
| `pi_search_pool_snapshot` | inspect one pool or rediscover pools by `run_id` |
| `pi_search_pool_continue` | state-redispatch a candidate with a fresh worker |
| `pi_search_pool_close` | drain or terminate live pool jobs |
| `pi_search_run_candidate` | synchronous single-candidate compatibility driver |
| `pi_search_run_batch` | synchronous batch compatibility driver |

Normal Pi Search uses the pool tools. The compatibility drivers remain useful
for recovery and focused debugging but wait for their entire call to finish.

Example read-only call:

```bash
goal-plus-pi-tool goal_plus_monitor_snapshot \
  --root .gp \
  --args-json '{"run_id":"run_..."}' \
  --pretty
```

## SearchSpec Fields That Control Execution

| Field | Meaning |
|---|---|
| `objective` | measurable optimization target |
| `metric_name`, `metric_direction` | ranking value and direction |
| `source_path` | baseline source snapshot |
| `editable_globs`, `forbidden_globs` | candidate edit surface |
| `process_verifiers` | correctness gates |
| `ranking_signals` | metric-producing commands |
| `promotion_verifiers` | checks required before promotion |
| `budget.max_candidates` | whole-run distinct candidate cap |
| `budget.max_parallel` | live-worker/planned-batch cap |
| `strategy.worker_host` | `pi-rpc`, `codex`, `claude-code`, or `opencode` |
| `strategy.worker_budget` | host-enforced limit for one dispatch |
| `workspace.backend` | `git_worktree` (default) or `copy` |

Every ranking command must exit successfully and print a final JSON object with
a finite numeric value under `metric_name`. Temporary verifier outputs belong
under `GOAL_PLUS_VERIFIER_TMPDIR`; verifier artifacts and evaluation inputs are
hash-pinned.

## Error Semantics

- Validation errors mean the caller must fix the spec or tool arguments.
- Candidate verifier failures are normal search evidence.
- `VerifierWorkspaceSideEffect` with infrastructure-failure metrics means the
  evaluator violated isolation; stop the candidate and repair/refreeze.
- Frozen artifact hash mismatches invalidate scoring.
- A main-confirmed verifier defect requires `search_invalidate_run`, then host
  interruption/quiescence, then a repaired frozen spec and successor run.
- Host timeouts and runner failures are different: a timeout proves deadline
  enforcement, while a runner failure requires host recovery evidence.

See [Flow](flow-view.md) for call ordering and [Design](design.md) for the state
and ownership model.
