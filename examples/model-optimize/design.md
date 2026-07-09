# Model Optimize Scenario Design

> Status: draft  
> Scope: use `/goal-plus` to optimize a whole model-serving target through
> recursive opportunity discovery and deepening, with Pi Agent as the preferred
> host. The first runnable target should be a CPU-only toy model; vLLM/NPU is a
> later stress target.

## 1. Goal

This scenario describes a recursive model-optimization workflow:

```text
/goal-plus "optimize <model target> under <workload>"
  -> discover optimization opportunities from baseline, profiling, traces, and code scan
  -> decide which opportunities deserve deeper exploration
  -> use single edits, local Search Mode, or kernel Search Mode as needed
  -> integrate accepted local results back into the model repository
  -> use one top-level E2E verifier to decide whether the whole-model goal improved
```

The important point is that Search Mode is not always entered at the beginning.
The root `/goal-plus` run first acts as an optimization scout. It only opens a
search run when it finds a bounded opportunity with a measurable metric, an
automated verifier, and multiple credible implementation choices.

This is meant to guide two things:

1. A practical Pi-first example flow that can be run with the current
   `/goal-plus` and Search MCP surfaces.
2. A direction for evolving `/goal-plus` from a single Search Mode upgrade into
   a recursive optimization control plane.

The first implementation should not start with a real vLLM model. Start with a
small CPU-only model-serving fixture where profiling, verification, and
candidate search are cheap and deterministic. That fixture should exercise the
same control logic that will later be used for vLLM: discover opportunities,
choose a deepening mode, run child searches, integrate a result, and run a root
E2E verifier.

## 2. Current Boundary

The current Search runtime already provides the pieces needed for local
optimization campaigns:

- frozen `SearchSpec` and verifier artifacts
- isolated candidate workspaces
- edit-surface and frozen-verifier anti-cheat checks
- verifier-owned scoring
- candidate history, best result selection, report, and promotion patch
- Pi RPC worker support with a hard process watchdog

It does not yet provide a full recursive supervisor:

- no first-class opportunity ledger API
- no first-class parent/child search relationship
- no runtime-owned worker heartbeat, wait, abort, or observation blackboard
- no resource scheduler for GPU/NPU benchmarks
- no git-worktree backend for large repositories
- no built-in benchmark noise model

Therefore the v0 design uses the Pi main agent as the recursive orchestrator.
The runtime remains strict and local: once a specific opportunity is frozen as a
search problem, Search MCP owns the candidate workspaces, verifier runs, scores,
history, and report for that child run.

Goal Plus itself should remain domain-neutral. It is a flexible goal loop and
search orchestration mechanism, not a built-in model-optimization expert. Model
serving knowledge, vLLM knowledge, profiler interpretation, NPU backend
constraints, kernel-generation rules, and CANN/Triton/TileLang details should
come from external skills, scenario packs, prompts, or tool adapters that the
root goal imports.

## 3. Core Thesis

Whole-model optimization and kernel generation should share one ledger, not one
completion authority.

Both are represented as `OpportunityUnit`s. They differ in deepening mode:

```text
runtime/scheduler opportunity -> source patch or local source search
graph/fusion opportunity      -> graph rewrite or subgraph search
kernel opportunity            -> kernel search plus integration patch
config/routing opportunity    -> single edit or local config/source search
```

Only the root whole-model verifier can decide final KEEP/REVERT. A kernel
microbenchmark, graph-level correctness check, or routing path-hit metric is
local evidence. It can justify integration, but it cannot declare the whole
model optimized.

## 4. Domain Skills Are External

This scenario depends on domain expertise, but that expertise should not be
hard-coded into Goal Plus.

Goal Plus owns:

- raw goal intake and progress toward the goal
- triage between Goal Mode, Spec Discovery, and Search Mode
- frozen standards for measurable search
- parent/child search orchestration conventions
- final audit against the original goal

Search MCP owns:

- frozen `SearchSpec`
- candidate workspaces
- verifier execution
- scoring history
- selection, report, and promotion artifacts

External skills own:

- how to profile a target such as a CPU toy model, vLLM, SGLang, or CANN
- how to interpret traces and identify opportunities
- how to define domain-specific edit surfaces
- how to build local verifiers and path-hit checks
- how to extract kernel or subgraph contracts
- how to integrate kernel artifacts safely

The root GoalContract should record external skill references so runs are
auditable and reproducible:

```yaml
domain_skills:
  - name: cpu-model-opt
    source: examples/model-optimize/skills/cpu-model-opt/SKILL.md
    role: baseline/profile/opportunity-discovery
    hash: "<optional sha256>"
  - name: vllm-serving-opt
    source: "<external skill path>"
    role: vllm source-patch guidance
    hash: "<optional sha256>"
  - name: kernel-optimize
    source: scenarios/kernel-optimize/README.md
    role: kernel child-search bootstrap
    hash: "<optional sha256>"
```

This keeps Goal Plus reusable. Adding vLLM, NPU, Triton-Ascend, or CANN support
should mean adding skills and scenario assets, not changing the core goal loop.

## 5. Top-Level Flow

```text
User
  |
  | /goal-plus optimize <model target> for <workload>
  v
Pi main agent
  |
  | goal_plus_create
  | goal_plus_record_triage(model-optimize, spec_discovery)
  v
Spec Discovery / Diagnosis
  |
  | freeze top-level GoalContract:
  |   model, source_commit, hardware, workload, correctness, metric, edit surface
  | run baseline + profile + trace + code scan
  | read imported domain skills
  v
Opportunity Discovery
  |
  | create Opportunity Ledger
  | rank opportunities by impact, confidence, tractability, risk, and cost
  v
Deepening Planner
  |
  +-- mode=single
  |     direct bounded patch + top-level verifier
  |
  +-- mode=source-search
  |     child SearchSpec over repo paths
  |     Pi RPC workers try competing patches
  |
  +-- mode=graph-search
  |     child SearchSpec over graph rewrite or custom-op integration
  |
  +-- mode=kernel-search
  |     extract kernel/subgraph contract
  |     child SearchSpec optimizes kernel artifact
  |     parent integrates artifact
  |
  +-- mode=defer
        record why no safe automated optimization exists yet

Integration / Champion Update
  |
  | apply accepted child result to integration workspace
  | run top-level process verifier + E2E verifier
  | keep protected champion if improved
  v
Final
  |
  | promotion verifier from clean checkout/worktree
  | optional Codex/Humanize review as soft review
  | report opportunity ledger + accepted/rejected evidence
```

## 6. Goal Contract

The root contract freezes the standard that every local optimization must
respect.

```yaml
kind: model-optimize-goal
target:
  repository: cpu-toy-model | vllm | sglang | custom
  source_path: /path/to/target
  source_commit: "<immutable commit>"
  model: "<model id or local path>"
  hardware: "cpu | GPU/NPU type and count"
  precision: "fp32 | bf16 | fp16 | fp8 | int8 | mixed"

workload:
  command: "<fixed benchmark command>"
  dataset: "<fixed prompt suite or trace>"
  request_shape_distribution: "<input/output/concurrency distribution>"
  sla: "<latency or quality target>"

metric:
  primary: "tokens_per_second_under_sla"
  direction: maximize
  secondary:
    - p50_latency_ms
    - p95_latency_ms
    - p99_latency_ms
    - ttft_ms
    - decode_tokens_per_sec
    - gpu_memory_gb
    - path_hit_rate

correctness:
  command: "<fixed correctness command>"
  quality_gate: "<golden outputs / tolerance / format checks>"
  held_out: "<optional hidden prompts or trace slice>"

edit_surface:
  allow:
    - "vllm/**"
    - "csrc/**"
    - "cmake/**"
  deny:
    - "benchmarks/**"
    - "tests/fixtures/**"
    - "_verifier/**"
    - ".search/**"

runtime_policy:
  root_host: pi
  worker_host: pi-rpc
  max_opportunities_active: 2
  max_child_searches: 4
  max_total_verifier_runs: 24
  require_top_level_e2e_before_keep: true

domain_skills:
  import_policy: external
  skill_refs:
    - "<path or package reference for target-specific optimization skill>"

resource_policy:
  mode: cpu-only | shared-gpu | shared-npu
  coordinator: scenario-file | external-service | none
  benchmark_exclusive: true
  resources:
    - id: cpu
      kind: cpu
      slots: 4
```

The root contract should not encode "try kernel fusion" or "change scheduler".
Those are hypotheses discovered later. The contract freezes standards, not
implementation ideas.

## 7. Resource Constraints And Negotiation

Real model optimization is often resource-limited. A vLLM or NPU run may have
one 8-card node, and different subagents must not accidentally benchmark on the
same cards at the same time.

The scenario should model resources explicitly:

```yaml
resource_manifest:
  node: npu-node-01
  devices:
    - id: npu0
      kind: npu
      memory_gb: 64
    - id: npu1
      kind: npu
      memory_gb: 64
    - id: npu2
      kind: npu
      memory_gb: 64
    - id: npu3
      kind: npu
      memory_gb: 64
    - id: npu4
      kind: npu
      memory_gb: 64
    - id: npu5
      kind: npu
      memory_gb: 64
    - id: npu6
      kind: npu
      memory_gb: 64
    - id: npu7
      kind: npu
      memory_gb: 64

resource_classes:
  smoke:
    devices: 1
    exclusive: false
  e2e_1card:
    devices: 1
    exclusive: true
  e2e_4card:
    devices: 4
    exclusive: true
  e2e_8card:
    devices: 8
    exclusive: true
```

The root Pi agent should act as the v0 resource coordinator:

- allocate device slices before launching child workers
- pass allocation through child `SearchSpec.constraints`
- set environment variables such as `CUDA_VISIBLE_DEVICES`, `ASCEND_RT_VISIBLE_DEVICES`,
  or backend-specific equivalents in verifier commands
- serialize expensive E2E promotion verifiers
- allow cheap static/unit checks to run concurrently
- keep baseline, champion, and candidate comparisons on the same resource class
- record device allocation in the opportunity ledger and final report

For an 8-card NPU node, default policy should be conservative:

```text
process/static checks       -> concurrent, no device or 1 shared smoke card
child local smoke benchmark -> at most 2 concurrent 1-card jobs
top-level E2E benchmark     -> exclusive allocation, usually all required cards
kernel microbenchmark       -> exclusive per worker for the requested card slice
```

Future Goal Plus evolution can move this from scenario convention into a
runtime resource API, but the first version can use a locked JSON file under:

```text
.search/goal-plus/<goal_plus_id>/model-optimize/resource_ledger.json
```

## 8. Opportunity Ledger

The opportunity ledger is the central abstraction of this scenario.

```yaml
opportunity:
  id: opp_003
  status: new | investigating | searching | integrated | accepted | rejected | deferred
  level: runtime | scheduler | graph | kernel | config | communication | memory
  location:
    files:
      - "vllm/worker/model_runner.py"
      - "vllm/attention/backends/..."
    symbols:
      - "<function or class>"
  evidence:
    profile_share: 0.087
    trace_refs:
      - ".search/goal-plus/<id>/model-opt/profile/nsys.json"
    path_hit_rate: 0.42
    failure_or_gap: "decode step has many small launch-bound kernels"
  hypothesis: "Fusing RMSNorm/residual around decode reduces launch density"
  expected_impact:
    metric: decode_tokens_per_sec
    direction: maximize
    rough_delta: "2-5%"
  risk:
    correctness: medium
    integration: high
    benchmark_noise: medium
  deepening:
    mode: single | source-search | graph-search | kernel-search | defer
    reason: "multiple implementation choices with local verifier"
    child_run_id: null
    requested_resources:
      class: smoke | e2e_1card | e2e_4card | e2e_8card | cpu
      exclusive: true
  local_verifier:
    command: "<optional local command>"
    metric: "<local metric>"
  final_gate: top_level_e2e
  decision_log:
    - time: "<timestamp>"
      decision: "defer"
      reason: "no stable subgraph boundary yet"
```

The main Pi agent maintains this ledger during Spec Discovery and throughout
recursive deepening. In v0 it can be a plain JSONL or Markdown file under:

```text
.search/goal-plus/<goal_plus_id>/model-optimize/opportunities.jsonl
.search/goal-plus/<goal_plus_id>/model-optimize/report.md
```

A future Goal Plus API can make this first-class, but the initial design should
not block on new runtime tools.

## 9. First Runnable Target: CPU Toy Model

The first target should be a CPU-only model fixture, not vLLM on a real model.
The goal is to debug the recursive control loop cheaply.

The CPU fixture should include:

- a tiny PyTorch model or toy serving loop
- a fixed synthetic workload
- a deterministic correctness gate
- a CPU benchmark that prints JSON metrics
- at least three seeded optimization opportunities
- one opportunity suitable for `single`
- one opportunity suitable for `source-search`
- one opportunity that looks like `kernel-search` but is deferred or mapped to
  a CPU local operator optimization

Example opportunities:

| Opportunity | Mode | Purpose |
|---|---|---|
| repeated preprocessing or token transform | single | test direct patch plus root verifier |
| batch collation / cache policy | source-search | test child Search Mode and Pi RPC workers |
| small matmul/activation chain | graph-search or defer | test graph/kernel classification without NPU |
| Python loop in hot path | source-search | test local verifier and E2E verifier separation |

This fixture should prove the workflow before adding vLLM-specific skills. Once
the CPU fixture works, vLLM becomes a domain skill and resource-scheduling
stress test, not the first debugging environment.

## 10. Opportunity Discovery

The main agent should discover opportunities using evidence, not vibes.

Discovery inputs:

- baseline E2E benchmark
- top-level correctness run
- request trace or serving trace
- profiler output, such as nsys, torch profiler, NPU profiler, or vLLM internal stats
- path-hit instrumentation for backends, fast paths, fallbacks, kernels, and graph replay
- code scan around scheduler, cache, attention backend, model runner, parallelism, and C++/CUDA/NPU extension paths

Initial opportunity types:

| Type | Evidence | Typical Deepening |
|---|---|---|
| scheduler/runtime | queueing imbalance, prefill/decode gap, high sync overhead | single or source-search |
| KV/cache/memory | paging overhead, low reuse, high allocation cost | source-search |
| backend/kernel routing | fast path not hit, wrong shape guard, fallback too often | single or source-search |
| graph/fusion | repeated subgraph, launch density, materialized intermediates | graph-search or kernel-search |
| local kernel | hotspot has extractable inputs/outputs and E2E share | kernel-search |
| communication | all-reduce/all-gather overlap gap, MoE dispatch/combine | source-search, graph-search, or defer |
| config | obvious workload-specific knob | single or small source-search |

Opportunity priority can start with a simple heuristic:

```text
priority = impact * confidence * tractability / (risk * cost)
```

Where:

- `impact` comes from profile share and estimated E2E sensitivity
- `confidence` comes from repeated evidence and path-hit confirmation
- `tractability` comes from bounded edit surface and verifier quality
- `risk` covers correctness, integration, and maintainability
- `cost` covers benchmark time, device scarcity, and worker budget

## 11. Deepening Policy

The deepening planner chooses how to act on each opportunity.

### 11.1 Single Mode

Use `single` when there is one obvious, low-risk fix.

Examples:

- fix a guard that prevents an existing fast path from being selected
- add missing instrumentation to prove path hit rate
- enable a documented backend path for the frozen workload

Rules:

- keep patch small
- run top-level process verifier
- run top-level E2E verifier before accepting
- record decision in the opportunity ledger

### 11.2 Source Search Mode

Use `source-search` when multiple source-level implementations are plausible.

Examples:

- alternative KV block table strategies
- scheduler policy variants
- batch splitting or merging strategies
- backend routing rules
- communication/compute overlap policy

This creates a child `SearchSpec` over the model repository or a narrowed
workspace. Pi RPC workers try competing source patches. The root agent adopts
only the selected child result into the integration workspace and then runs the
top-level verifier.

Minimal child spec shape:

```json
{
  "objective": "Improve opportunity opp_004: KV cache block table overhead",
  "metric_name": "decode_tokens_per_sec",
  "metric_direction": "maximize",
  "source_path": "/path/to/vllm",
  "edit_surface": {
    "allow": [
      "vllm/core/**",
      "vllm/worker/**",
      "vllm/attention/**"
    ],
    "deny": [
      "benchmarks/**",
      "tests/fixtures/**",
      "_verifier/**",
      ".search/**"
    ],
    "max_file_changes": 8
  },
  "process_verifiers": [
    {
      "name": "targeted_tests",
      "role": "process_gate",
      "command": ["python", "-m", "pytest", "tests/<targeted>", "-q"],
      "timeout_seconds": 600
    },
    {
      "name": "smoke_serving",
      "role": "ranking_signal",
      "command": ["python", "_verifier/model_opt_smoke.py", "--json"],
      "timeout_seconds": 1200
    }
  ],
  "promotion_verifiers": [
    {
      "name": "fixed_e2e_benchmark",
      "role": "promotion_gate",
      "command": ["python", "_verifier/fixed_e2e_benchmark.py", "--json"],
      "timeout_seconds": 3600
    }
  ],
  "budget": {"max_candidates": 4, "max_parallel": 2},
  "constraints": {
    "parent_goal_plus_id": "<goal_plus_id>",
    "opportunity_id": "opp_004",
    "level": "runtime",
    "final_gate_required": "top_level_e2e",
    "domain_skill_refs": ["<external skill path or id>"],
    "resource_request": {
      "class": "cpu",
      "exclusive": false
    }
  },
  "strategy": {
    "name": "agent_guided",
    "driver": "builtin",
    "worker_mode": "agent-session-pool",
    "worker_host": "pi-rpc",
    "worker_budget": {
      "max_runtime_seconds": 1800,
      "max_turns": 8,
      "on_exceed": "interrupt"
    },
    "history_policy": {"scope": "top_n", "top_n": 5}
  }
}
```

### 11.3 Graph Search Mode

Use `graph-search` when the opportunity is a graph rewrite, subgraph
replacement, or custom-op integration problem.

Examples:

- selecting which FX subgraphs should be replaced
- testing whether a custom op blocks downstream compiler fusion
- trying alternative shape guards or graph replay boundaries

Graph search must produce an integration patch, not only a graph-local score.
The local verifier can check graph correctness and path-hit evidence, but the
root E2E verifier still decides KEEP/REVERT.

### 11.4 Kernel Search Mode

Use `kernel-search` only when the kernel opportunity is extractable and likely
to matter to E2E performance.

Trigger requirements:

- profile evidence shows meaningful E2E share or launch-density impact
- inputs, outputs, dtype, layout, shape distribution, tolerance, and fallback are known
- the parent integration path is known before search starts
- the candidate cannot win by changing benchmark, workload, or wrapper contract
- local kernel speed is expected to survive integration overhead

The kernel child run should use the existing `kernel-optimize` scenario or a
CANNBench-style generated SearchSpec. The output is a `KernelArtifact`, not a
whole-model success.

```yaml
kernel_artifact:
  opportunity_id: opp_007
  child_run_id: run_...
  status: success | failed | timeout | partial
  backend: triton | triton_ascend | tilelang_ascend | ascendc | cpp
  artifact_paths:
    - "kernels/fused_norm_residual.py"
  wrapper_paths:
    - "vllm/model_executor/custom_ops/..."
  shape_guards:
    - "batch in {1,2,4,8} and hidden=4096"
  correctness_report: ".search/runs/.../report.md"
  local_benchmark:
    metric: avg_latency_ms
    baseline: 0.092
    candidate: 0.051
  integration_rule:
    patch_path: "integration/opp_007.patch"
    fallback: "use original aten path when guard misses"
  final_gate: top_level_e2e
```

The parent agent integrates the artifact into a parent integration workspace,
runs path-hit checks, then runs the top-level E2E verifier. If the E2E score
does not improve, the kernel artifact remains local evidence and is not
promoted.

### 11.5 Defer Mode

Use `defer` when the opportunity is real but cannot be safely automated yet.

Common reasons:

- no reliable verifier
- too much benchmark noise
- edit surface too broad
- graph boundary has aliasing or mutation that cannot be represented yet
- hardware queue or benchmark cost is too high
- expected impact is below threshold

Deferred opportunities are not failures. They are future backlog items with
evidence.

## 12. How Kernel Optimization Fits The Same Layer

The common layer is `OpportunityUnit`.

```text
OpportunityUnit
  |
  +-- source patch result
  +-- graph rewrite result
  +-- kernel artifact result
  +-- config/routing result
```

Each result has:

- hypothesis
- evidence
- local verifier, if any
- integration rule
- rollback path
- final top-level gate

This keeps kernel generation inside the whole-model optimization story without
letting local kernel scores hijack the root objective.

Correct mental model:

```text
kernel search is a deepening mode of one opportunity
```

Incorrect mental model:

```text
kernel search is a sibling top-level optimizer that can declare the model goal done
```

## 13. Pi-First Implementation Logic

Pi is preferred for this scenario because it has both:

- a main-agent extension surface for `/goal-plus`
- a `pi-rpc` worker host with a hard `worker_budget.max_runtime_seconds`

### 13.1 Root Pi Prompt

Example user prompt:

```text
/goal-plus Optimize the CPU toy model under this fixed workload.
Use the model-optimize scenario.
First discover optimization opportunities from baseline, profiler, and code scan.
Import domain knowledge only from the model-optimize CPU skill.
Open child searches only for opportunities with bounded edit surface,
automated verifier, and multiple credible approaches.
Use Pi RPC workers for child searches.
All local improvements must pass the final fixed E2E verifier before KEEP.
```

Later vLLM prompt shape:

```text
/goal-plus Optimize vLLM for Qwen3-8B under this fixed serving workload.
Use the model-optimize scenario.
Import vLLM, serving benchmark, profiler, and kernel optimization skills from
the external skill references in the GoalContract.
Coordinate NPU resources through the scenario resource ledger.
Open child searches only for bounded opportunities with verifier evidence.
All local improvements must pass the final fixed E2E verifier before KEEP.
```

### 13.2 Main Agent Responsibilities

The Pi main agent should:

1. Call `goal_plus_create`.
2. Record triage as optimization-shaped and scenario `model-optimize`.
3. Enter Spec Discovery instead of immediately freezing a SearchSpec.
4. Freeze the root GoalContract in a scenario-local file.
5. Run baseline, correctness, profile, trace, and code scan.
6. Load the external domain skills listed in the GoalContract.
7. Write the opportunity ledger.
8. Reserve resources for any verifier or child search that needs them.
9. Select one or two top opportunities for deepening.
10. For each child search, freeze a child `SearchSpec` with
   `strategy.worker_host="pi-rpc"`.
11. Run `search_plan_next`, `search_start_batch`, `search_start_agent_session`,
   `pi_rpc_run_worker`, `search_bind_agent_handle`, and final
   `search_run_verifier`.
12. Integrate selected child result into the parent integration workspace.
13. Run the root E2E verifier on the reserved resource class.
14. Update the protected champion and opportunity ledger.
15. Stop when budget is exhausted, target is met, or no high-value opportunity
    remains.
16. Produce a final report and optional promotion patch.

### 13.3 Pi Worker Responsibilities

Pi RPC workers should remain narrow. They receive one candidate workspace and
one local objective. They should not try to own the whole model goal.

Worker rules:

- call `search_get_agent_context` first
- edit only the candidate workspace
- respect allowed and denied files
- create a candidate artifact early
- call `search_run_verifier` early and after meaningful edits
- report changed files, verifier score, and blockers

For model optimization, the worker prompt should additionally say:

```text
You are optimizing one opportunity, not the whole model.
Your local verifier is evidence only.
Do not claim final success.
The parent goal will run the fixed E2E verifier after integration.
Prefer small, reversible patches with path-hit instrumentation.
```

For kernel optimization, it should say:

```text
You are producing a KernelArtifact for one opportunity.
Do not change the wrapper contract, reference implementation, verifier,
benchmark, or workload.
If the artifact is locally faster, still report integration risks and guard
coverage. The parent E2E verifier decides whether it is accepted.
```

## 14. Current v0 Without Runtime Changes

This scenario can start without new Search MCP APIs:

1. Use `/goal-plus` in Pi for the root goal.
2. Keep the root run in Spec Discovery / Goal Mode while diagnosing.
3. Store the root GoalContract and Opportunity Ledger under `.search/goal-plus`.
4. For each deepened opportunity, create a normal child Search run and include
   `parent_goal_plus_id` and `opportunity_id` in `SearchSpec.constraints`.
5. Load domain-specific optimization knowledge from external skill files.
6. Coordinate resources in a scenario-local `resource_ledger.json` if a child
   run needs GPU/NPU hardware.
7. Use Pi RPC workers for child runs.
8. Let the main Pi agent integrate selected child results and run the root E2E
   verifier.
9. Produce one final report that links child run IDs and explains accepted,
   rejected, and deferred opportunities.

This is enough to validate the workflow shape.

Limitations of v0:

- parent/child search links are conventions in `constraints`, not first-class runtime state
- the opportunity ledger is a scenario file, not a runtime object
- the main agent must manually avoid runaway recursive searches
- child search promotion patches are not automatically applied to parent
  integration workspaces
- GPU/NPU resource locking is external
- benchmark noise handling lives in verifier scripts
- domain skills are external files and must be versioned or hash-recorded by the
  scenario

## 15. Initial Development Target

Do not begin implementation with vLLM or an 8-card NPU benchmark. Begin with a
CPU-only toy model because it makes the recursive loop observable and cheap.

The intended progression is:

```text
CPU toy model
  -> CPU toy model with child source-search
  -> CPU toy model with graph/kernel-classified opportunity
  -> small single-GPU or single-NPU model fixture
  -> vLLM source-search on a small workload
  -> vLLM/NPU resource-coordinated campaign
```

This ordering prevents vLLM environment setup, hardware scheduling, and kernel
integration complexity from hiding bugs in the Goal Plus control logic.

## 16. Suggested `/goal-plus` Evolution

This scenario suggests the next evolution of `/goal-plus`.

### Phase 0: Documentation And Example

- add this design document
- add a model-optimize prompt example
- define root GoalContract and Opportunity Ledger templates
- define source-search and kernel-search child `SearchSpec` templates

### Phase 1: Scenario Pack

Add `examples/model-optimize/` assets:

```text
examples/model-optimize/
  design.md
  README.md
  templates/
    goal_contract.yaml
    opportunity_ledger.jsonl
    resource_manifest.yaml
    resource_ledger.json
    source_search_spec.json
    kernel_search_spec.json
  prompts/
    pi-root.md
    pi-source-worker.md
    pi-kernel-worker.md
```

This remains declarative. It should not fork Search MCP runtime behavior.

### Phase 2: Child Search Linking

Add lightweight Goal Plus APIs or conventions:

```text
goal_plus_record_evidence(goal_plus_id, evidence)
goal_plus_record_opportunity(goal_plus_id, opportunity)
goal_plus_link_child_search(goal_plus_id, opportunity_id, run_id)
goal_plus_record_opportunity_decision(goal_plus_id, opportunity_id, decision)
goal_plus_record_resource_event(goal_plus_id, resource_event)
```

This turns the scenario ledger into durable goal state.

### Phase 3: Recursive Deepening Support

Add first-class support for recursive search orchestration:

- parent/child run graph
- opportunity status and decision log
- artifact adoption workflow
- integration workspace
- root E2E gate after child adoption
- budget accounting across child runs

The Search runtime still owns local candidate scoring. Goal Plus owns the
recursive optimization graph.

### Phase 4: Supervisor And Resource Control

For real vLLM/NPU optimization, add:

- git-worktree candidate backend
- benchmark resource lock
- run-level and child-search budgets
- worker heartbeat/status/observation APIs
- wait/abort/finalize support where host surfaces permit it
- benchmark repetition and confidence gates
- first-class resource requests, leases, and release events

Without this phase, long-running model optimization can work as a guided
workflow, but not as a robust autonomous campaign.

## 17. Failure Modes And Defenses

| Failure Mode | Symptom | Defense |
|---|---|---|
| premature search | search starts before metric/verifier is meaningful | require Spec Discovery and root GoalContract |
| opportunity hallucination | agent invents optimization points without profile evidence | every opportunity needs evidence refs |
| local metric trap | kernel or graph local score improves but E2E regresses | root E2E verifier before KEEP |
| benchmark hacking | worker edits workload or verifier | deny files and frozen verifier artifacts |
| path-hit fake success | code patch exists but production path never uses it | require path-hit instrumentation |
| graph barrier regression | custom op blocks compiler fusion | record fallback/path-hit and run E2E |
| recursive runaway | too many child searches consume device budget | root budget and max child searches |
| benchmark noise | one lucky run wins | repeat verifier, compare baseline/champion/candidate in same run |
| large-repo overhead | candidate copy is too expensive | git-worktree backend |
| kernel integration gap | artifact is fast but hard to call safely | require integration rule before kernel search |
| hidden domain coupling | Goal Plus starts baking in vLLM/CANN rules | keep domain knowledge in external skills |
| device contention | subagents benchmark on the same NPU cards | resource ledger, leases, exclusive E2E runs |
| starting too large | vLLM/NPU complexity hides control-loop bugs | CPU-first fixture and staged escalation |

## 18. Minimal Acceptance Criteria

A first runnable model-optimize scenario does not need to beat vLLM SOTA. It
only needs to prove the control shape:

- root Pi `/goal-plus` creates a model-optimize goal
- target is a CPU-only toy model fixture
- main agent imports the CPU model optimization skill externally
- main agent runs baseline/profile and writes at least three opportunities
- at least one opportunity is deepened with `source-search` using Pi RPC
- at least one opportunity is either deferred or classified as kernel-search
  with a clear extraction contract
- selected child result is integrated into a parent workspace
- root E2E verifier decides accepted/rejected
- final report lists opportunities, child run IDs, local scores, E2E scores,
  accepted patches, rejected patches, deferred work, and resource usage

The second acceptance target is a kernel child run:

- profile identifies a local kernel/subgraph opportunity
- host extracts a verifier workspace
- child Search Mode optimizes the kernel artifact
- parent integrates the artifact behind a guard/fallback
- root E2E verifier decides whether it is kept

The third acceptance target is a resource-constrained run:

- declare an 8-card resource manifest, even if using a local mock
- run two child workers with non-overlapping resource requests
- serialize root E2E verification
- report resource allocation and release events

## 19. Final Design Principle

`/goal-plus` should become an optimization scout and recursive deepener.

Search Mode remains the tool for bounded, verifier-backed local exploration.
Kernel generation is one possible local exploration mode. Whole-model E2E
verification remains the final authority.

Domain knowledge remains outside Goal Plus. Goal Plus should make it easy to
import, audit, and apply such knowledge, but it should stay a generic goal loop
with measurable-search upgrades.
