# Design

## Objective

This project provides a generic Search MCP Runtime for measurable coding tasks. The runtime owns durable state and verification. The host agent owns planning and progress control through MCP tool calls. A skill guides the host agent so the process is repeatable in OpenCode without hard-coding OpenCode into the runtime.

V0 intentionally focuses on the control plane:

- freeze a `SearchSpec` and verifier artifacts before exploration
- create isolated candidate workspaces under `.search/runs/<run_id>/workspace/<candidate_id>/`
- express the active search strategy through a durable next-step plan
- accept candidate artifacts from a host agent or future worker adapter
- run verifier commands from the runtime
- summarize candidate history for follow-up planning
- select the best verified candidate
- export a report and promotion patch

## Architecture

```text
User
  |
  | /search ...
  v
OpenCode host agent
  |
  | reads .opencode/skills/search/SKILL.md
  | calls MCP tools
  v
Search MCP server
  |
  | delegates JSON tool calls
  v
SearchTools facade
  |
  | validates API payloads
  v
FileSearchRuntime
  |
  | writes durable state
  v
.search/
  specs/<frozen_spec_id>/
  runs/<run_id>/
    run.json
    plans/<plan_id>.json
    dispatches/<dispatch_id>.json
    dispatches/<dispatch_id>.md
    candidates/<candidate_id>/candidate.json
    candidates/<candidate_id>/task.json
    workspace/<candidate_id>/
    report.md
    promotion/<candidate_id>.patch
```

## Control Split

The runtime is the stateful control plane. It decides what is frozen, where workspaces live, which candidate files changed, whether a verifier passed, and which candidate can be promoted.

The skill is the host-side workflow policy. It tells the main agent to freeze the spec before creating candidates, avoid editing the main workspace, submit every candidate through MCP, and trust runtime verifier results instead of worker claims.

The worker is deliberately thin in V0. The main agent can edit candidate workspaces directly. If a subagent or worker is used, dispatch is a two-channel protocol: the main agent gives an explicit directive, and the worker calls MCP to fetch authoritative runtime context by `dispatch_id`.

## Core Data Model

`SearchSpec` describes one search job:

- `objective`: what the search is trying to improve
- `metric_name`: primary metric extracted from verifier output
- `metric_direction`: `maximize` or `minimize`
- `source_path`: project or subdirectory copied into candidate workspaces
- `edit_surface`: allowed and denied files
- `process_verifiers`: commands used to rank or gate candidates
- `promotion_verifiers`: final anti-cheat or release checks
- `budget`: candidate count, parallelism, and time limits
- `root_hypotheses`: optional starter hypotheses for candidate workspaces

`FrozenSpec` is produced by `search_freeze_spec`. It stores the canonical spec hash and hashes of verifier artifacts. The runtime also copies verifier artifacts into `.search/specs/<id>/frozen_verifiers/` for auditability.

`StrategySpec` describes the run-level search mode. It can be a legacy string such as `independent_branches`, or a structured object with:

- `name`: strategy mode, for example `agent_guided`, `evolve`, `mcts`, or a custom name
- `driver`: `builtin`, `python`, or `external_mcp`
- `worker_mode`: `main-agent-search-direct`, `sub-agent-search-dispatch`, or `auto`
- `worker_agent_type`: optional host-adapter hint for the worker agent name, for example OpenCode `subagent_type="AnySearchAgent"`
- `worker_timeout_seconds`: default per-candidate worker timebox, defaulting to 600 seconds; `search_prepare_worker(..., timeout_seconds=...)` may override it for one dispatch
- `worker_local_verifier_max_runs`: maximum number of worker-local verifier/scorer calls during one candidate exploration, defaulting to 0 so actual verification is main-agent/runtime-owned
- `history_policy`: the official history view returned to the host agent
- `parent_policy` and `config`: strategy-specific settings

The runtime does not pretend to erase the main agent's chat memory. Instead, it returns an official strategy plan that says which candidates the current mode selected as parents, inspirations, or frontier nodes. Candidate lineage is then recorded and validated through plan/proposal metadata.

`SearchPlan` is produced by `search_plan_next`. It is the strategy step API. It contains the active strategy, worker policy, requested/planned batch size, official history view, derivation policy, optional proposal contract, fixed work orders, and strategy trace. Plans are written to `.search/runs/<run_id>/plans/<plan_id>.json`.

`CandidateProposal` is submitted to `search_start_batch` when the strategy requires the host agent to propose candidates. Agent-guided strategies use this path: the runtime returns a proposal contract, and the host submits parent IDs, intent, expected tradeoff, and instructions.

`CandidateTask` is produced by `search_start_batch` or the compatibility helper `search_next_batch`. It contains the candidate workspace path, allowed files, denied files, parent/base candidate IDs, plan ID, proposal metadata, and local instructions.

`WorkerDispatch` is produced by `search_prepare_worker`. It records the main agent's worker-facing directive, the immutable context snapshot returned to the worker, a context hash, and a markdown brief. Dispatch files are written to `.search/runs/<run_id>/dispatches/`. A worker should call `search_get_worker_context(dispatch_id)` before editing so it does not depend on chat context for workspace, verifier, strategy, lineage, or scratch-directory details.

`ArtifactBundle` is submitted by the host after editing a candidate workspace. The runtime independently detects changed files and verifier results; the bundle summary is not trusted as a score. If worker dispatch was used, the bundle can include `dispatch_id` and `context_hash`; the runtime validates that they belong to the candidate.

`ScoreReport` is produced by `search_run_verifier`. It records pass/fail state, aggregate score, raw metrics, changed-file violations, and failure class.

`search_list_history` returns a compact JSON view of the current run. It is intended for review, debugging, and reporting: candidates are sorted by score by default, limited by `top_n`, and include artifact summaries, scores, key metrics, changed files, failures, lineage, strategy metadata, and log paths.

`search_prepare_worker` and `search_get_worker_context` implement the two-channel worker protocol. The main agent records the task-specific directive through `search_prepare_worker`; the subagent retrieves the authoritative context through `search_get_worker_context`. This makes worker dispatch auditable and lets future worker adapters keep the same API. When `strategy.worker_mode` is `sub-agent-search-dispatch`, candidate submission requires a matching `dispatch_id` and `context_hash`.

## Strategy Modes

Every run has a strategy contract owned by the runtime. The current built-in modes are:

- `independent_branches`: each candidate starts from the frozen source workspace. This preserves the original V0 behavior.
- `agent_guided`: the runtime returns an official history view and a proposal contract. The main agent decides the next candidate proposals, then calls `search_start_batch`.
- `evolve`: the runtime selects the best verified parent and top inspirations, then creates follow-up work orders derived from that parent. This approximates the fixed parent/inspiration selection used by OpenEvolve.
- `mcts`: a placeholder tree-search mode that exposes the same frontier-expansion contract. In V0 it expands the best verified candidate; a fuller UCB/tree policy can replace the planner.

Custom strategy entry points:

- `driver: "python"` with `ref: "module:Class"` loads a local Python strategy object. The object is constructed with `strategy.config` and must implement `plan_next(payload) -> dict`. The payload includes the run record, full spec, full created-order history, requested batch size, planned batch size, and remaining budget.
- `driver: "external_mcp"` is represented through the standard proposal contract. Call the external strategy separately, then pass its proposals to `search_start_batch`.

The important split is:

- Strategy internal access can use full runtime state.
- The host agent receives the official strategy plan for this step.
- Candidate creation must satisfy that plan's derivation/proposal policy.

## State Flow

```text
draft SearchSpec
  |
  v
search_freeze_spec
  |
  v
search_create
  |
  v
search_plan_next
  |
  v
search_start_batch
  |
  v
search_prepare_worker  (required for sub-agent-search-dispatch)
  |
  v
search_get_worker_context  (subagent/worker first step)
  |
  v
candidate workspace edits
  |
  v
search_submit_candidate
  |
  v
search_run_verifier
  |
  v
search_plan_next  (optional follow-up batch)
  |
  v
search_select
  |
  v
search_report
  |
  v
search_promote
```

Promotion writes a patch. It does not mutate the original source workspace.

`search_next_batch(run_id, k)` remains as a compatibility helper. It calls `search_plan_next` and immediately starts the batch for strategies that produce fixed work orders. For `agent_guided`, use `search_plan_next` followed by `search_start_batch` with explicit proposals.

## Verification Model

Verifier commands run from each candidate workspace. The runtime adds the candidate workspace to `PYTHONPATH` and parses the last JSON object printed to stdout as metrics.

For example, the toy verifier prints:

```json
{"combined_score": 1.0}
```

The runtime extracts `metric_name` from that JSON object and uses it as the candidate score. Hard gates such as edit-surface violations and frozen verifier hash failures force the score to `0.0`.

## Isolation Model

V0 assumes there is no external sandbox. Isolation is achieved by copying `source_path` into per-candidate workspaces:

```text
.search/runs/<run_id>/workspace/c001/
.search/runs/<run_id>/workspace/c002/
```

Each candidate workspace contains a workspace-local `.tmp/` directory. Runtime tree hashing ignores `.tmp/`, so scratch files do not pollute changed-file detection or promotion patches. In dispatch mode, `.tmp` is only for notes, static drafts, and non-scoring helper material; workers should not create or run scratch experiment scripts, scorer clones, validation harnesses, parameter sweeps, or benchmark scripts.

The main workspace is not modified during exploration. Each candidate can be inspected, submitted, verified, and promoted independently.

This is enough for deterministic toy and control-plane tests. Future sandboxed execution can preserve the same API while changing how candidate workers are launched.

For `main-agent-search-direct`, the host agent can edit candidate workspaces directly. For `sub-agent-search-dispatch`, call `search_prepare_worker` for every candidate and pass the resulting `dispatch_id` to the worker. If `worker_policy.subagent_type` is present, an OpenCode host should use it as the Task tool's `subagent_type`. The worker should call `search_get_worker_context` as its first step, use `context.workspace` as the working directory, and treat `context.scratch_dir` as the only scratch area. The worker context includes `timeout_seconds`, `deadline_at`, and `local_validation_policy`; the host should collect best-so-far artifacts by that deadline. By default `local_verifier_max_runs=0`, so workers should not run the process verifier, evaluator APIs, equivalent scorers, score-driven sweeps, or custom scratch scripts that execute the candidate to estimate quality; non-scoring static checks such as `py_compile` are allowed. This is a runtime protocol deadline for the host/adapter; the V0 MCP server does not kill OpenCode subagent processes itself. The adapter should submit artifacts back to the runtime instead of copying files into the source workspace.

Verifier execution is runtime-owned. Workers may run local sanity checks, but final selection must use `search_run_verifier` results from the main agent/runtime flow. Frozen verifier and denied files are checked by the runtime, and candidate submission records denied-file touches or integrity failures instead of trusting worker claims.

## Implemented Modules

- `models.py`: strict Pydantic models for specs, candidates, artifacts, scores, and run records
- `runtime.py`: file-backed state machine, workspace copy, verifier execution, selection, report, patch export
- `tools.py`: JSON-friendly facade used by both tests and MCP
- `server.py`: FastMCP stdio server for OpenCode
- `.opencode/skills/search/SKILL.md`: host-agent workflow guide
- `examples/k_module_search_spec.json`: concrete toy SearchSpec

## V0 Boundaries

Implemented:

- MCP tool surface
- frozen verifier hashes
- candidate workspace creation
- edit surface checks
- verifier command execution
- metric extraction from JSON stdout
- compact candidate history API
- strategy planning API and candidate lineage records
- durable worker dispatch/context protocol
- best-candidate selection across verified candidates
- markdown report with plan, summary, metrics, and promotion patch
- unit tests, mock tests, and a k_module control-plane fixture
- OpenCode config and `/search` skill

Not implemented yet:

- automatic native OpenCode subagent spawning
- external sandbox orchestration
- distributed worker queue
- full adaptive search algorithms beyond the built-in plan contracts
- rich verifier artifact archive
- benchmark suite beyond the bundled local examples
