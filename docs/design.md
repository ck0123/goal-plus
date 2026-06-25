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
    candidates/<candidate_id>/candidate.json
    candidates/<candidate_id>/task.json
    workspace/<candidate_id>/
    report.md
    promotion/<candidate_id>.patch
```

## Control Split

The runtime is the stateful control plane. It decides what is frozen, where workspaces live, which candidate files changed, whether a verifier passed, and which candidate can be promoted.

The skill is the host-side workflow policy. It tells the main agent to freeze the spec before creating candidates, avoid editing the main workspace, submit every candidate through MCP, and trust runtime verifier results instead of worker claims.

The worker is deliberately thin in V0. The main agent can edit candidate workspaces directly. Later, a worker adapter can spawn headless agents or human-assisted workers, but it should still submit artifacts back to the same MCP runtime.

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
- `history_policy`: the official history view returned to the host agent
- `parent_policy` and `config`: strategy-specific settings

The runtime does not pretend to erase the main agent's chat memory. Instead, it returns an official strategy plan that says which candidates the current mode selected as parents, inspirations, or frontier nodes. Candidate lineage is then recorded and validated through plan/proposal metadata.

`SearchPlan` is produced by `search_plan_next`. It is the strategy step API. It contains the active strategy, requested/planned batch size, official history view, derivation policy, optional proposal contract, fixed work orders, and strategy trace. Plans are written to `.search/runs/<run_id>/plans/<plan_id>.json`.

`CandidateProposal` is submitted to `search_start_batch` when the strategy requires the host agent to propose candidates. Agent-guided strategies use this path: the runtime returns a proposal contract, and the host submits parent IDs, intent, expected tradeoff, and instructions.

`CandidateTask` is produced by `search_start_batch` or the compatibility helper `search_next_batch`. It contains the candidate workspace path, allowed files, denied files, parent/base candidate IDs, plan ID, proposal metadata, and local instructions.

`ArtifactBundle` is submitted by the host after editing a candidate workspace. The runtime independently detects changed files and verifier results; the bundle summary is not trusted as a score.

`ScoreReport` is produced by `search_run_verifier`. It records pass/fail state, aggregate score, raw metrics, changed-file violations, and failure class.

`search_list_history` returns a compact JSON view of the current run. It is intended for review, debugging, and reporting: candidates are sorted by score by default, limited by `top_n`, and include artifact summaries, scores, key metrics, changed files, failures, lineage, strategy metadata, and log paths.

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

The main workspace is not modified during exploration. Each candidate can be inspected, submitted, verified, and promoted independently.

This is enough for deterministic toy and control-plane tests. Future sandboxed execution can preserve the same API while changing how candidate workers are launched.

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
