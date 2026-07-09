# Model Optimize Scenario Implementation Plan

> Status: draft  
> Priority: Pi-first, CPU-first, domain-skills-external.

## 0. Design Commitments

This plan implements the scenario described in `design.md`.

Non-negotiable constraints:

- Goal Plus is a generic goal loop with measurable-search upgrades. It should
  not contain vLLM, CANN, NPU, Triton, TileLang, or profiler-specific knowledge.
- Domain knowledge comes from external skills, scenario files, prompts, and
  adapters referenced by the GoalContract.
- The first runnable target is a CPU-only toy model, not a real vLLM model.
- Pi is the preferred host: Pi main agent owns root orchestration, Pi RPC
  workers execute child searches.
- Whole-model/root E2E verification is the final authority. Local source,
  graph, or kernel scores are evidence, not completion.
- Resource-constrained runs must make device allocation explicit. On an 8-card
  NPU node, subagents coordinate through a resource ledger before running
  device-consuming verifiers.

## 1. Phase 0: Documentation Baseline

Goal: make the design actionable before writing runtime code.

Deliverables:

```text
examples/model-optimize/
  design.md
  plan.md
```

Tasks:

1. Keep `design.md` focused on architecture and Goal Plus evolution.
2. Keep `plan.md` focused on implementation order and acceptance checks.
3. Cross-link future files as they are added.

Acceptance:

- `design.md` states that domain skills are external.
- `design.md` states that the first runnable target is CPU-only.
- `design.md` states that resource coordination is required for shared GPU/NPU
  hardware.

## 2. Phase 1: CPU Toy Model Fixture

Goal: create the smallest target that exercises the recursive optimization loop
without vLLM or accelerator dependencies.

Directory:

```text
examples/model-optimize/cpu-toy/
  README.md
  target/
    model.py
    serving_loop.py
    workload.json
    benchmark.py
    correctness.py
    profile.py
  _verifier/
    root_e2e.py
    source_smoke.py
  expected/
    baseline_metrics.json
```

Fixture shape:

- CPU-only Python/PyTorch code.
- Fixed synthetic workload.
- Deterministic correctness gate.
- Benchmark prints one final JSON object for Search MCP parsing.
- At least three known opportunities are present but not hard-coded into Goal
  Plus.

Seed opportunities:

| ID | Kind | Example | Expected Mode |
|---|---|---|---|
| cpu_001 | runtime | repeated preprocessing in serving loop | single |
| cpu_002 | memory/cache | avoid recomputing repeated prompt features | source-search |
| cpu_003 | graph/local-op | Python loop around activation chain | source-search or graph-search |
| cpu_004 | kernel-like | tiny matmul+activation local path | defer or local CPU op search |

Tasks:

1. Write the toy target.
2. Write `correctness.py` with deterministic output checks.
3. Write `benchmark.py` with stable JSON metrics:

   ```json
   {
     "tokens_per_second": 123.4,
     "latency_ms": 8.1,
     "valid": true
   }
   ```

4. Write `profile.py` that emits simple evidence for the seeded opportunities.
5. Write `_verifier/root_e2e.py` for parent/root E2E verification.
6. Write `_verifier/source_smoke.py` for child source-search scoring.

Acceptance:

- Running root correctness succeeds on the baseline.
- Running root benchmark emits parseable JSON.
- Running profile emits evidence references for at least three opportunities.
- No GPU/NPU is required.

## 3. Phase 2: External CPU Domain Skill

Goal: prove that domain knowledge is imported, not built into Goal Plus.

Directory:

```text
examples/model-optimize/skills/
  cpu-model-opt/
    SKILL.md
```

Skill responsibilities:

- explain how to run the CPU toy baseline
- explain how to run profile and interpret opportunity evidence
- define CPU toy edit surfaces
- define allowed deepening modes
- define what counts as root E2E success
- define what should be deferred

Out of scope for the skill:

- Goal Plus lifecycle semantics
- Search MCP internals
- Pi RPC worker protocol
- generic candidate scoring

Tasks:

1. Write `SKILL.md`.
2. Include a short opportunity-classification rubric.
3. Include a root GoalContract example that references this skill.
4. Include the expected file paths for correctness, benchmark, and profile.

Acceptance:

- `design.md` and templates reference the skill by path.
- A human can understand the toy model optimization domain by reading only the
  skill and fixture README.
- No CPU toy logic is added to Goal Plus core code.

## 4. Phase 3: Scenario Templates

Goal: make the scenario runnable by a Pi main agent without hand-writing every
spec.

Directory:

```text
examples/model-optimize/templates/
  goal_contract.cpu.yaml
  opportunity_ledger.empty.jsonl
  resource_manifest.cpu.yaml
  resource_manifest.npu8.yaml
  source_search_spec.cpu.json
  kernel_search_spec.placeholder.json
```

Template requirements:

- `goal_contract.cpu.yaml` references the CPU domain skill externally.
- `opportunity_ledger.empty.jsonl` shows the schema but starts empty.
- `resource_manifest.cpu.yaml` declares CPU slots.
- `resource_manifest.npu8.yaml` declares eight NPU devices and resource
  classes.
- `source_search_spec.cpu.json` uses `worker_host="pi-rpc"` and a required
  `worker_budget.max_runtime_seconds`.
- `kernel_search_spec.placeholder.json` is non-runnable until a kernel contract
  is extracted.

Tasks:

1. Write templates with placeholders.
2. Keep all SearchSpec examples valid JSON.
3. Put parent links in `constraints`:

   ```json
   {
     "parent_goal_plus_id": "<goal_plus_id>",
     "opportunity_id": "cpu_002",
     "domain_skill_refs": [
       "examples/model-optimize/skills/cpu-model-opt/SKILL.md"
     ],
     "final_gate_required": "top_level_e2e"
   }
   ```

Acceptance:

- Templates can be copied into a working directory and filled by a Pi main
  agent.
- CPU source-search spec can be frozen by Search MCP once placeholders are
  replaced.
- Resource manifests are plain files, not runtime-specific code.

## 5. Phase 4: Pi Prompt Pack

Goal: guide Pi main and worker agents through the scenario.

Directory:

```text
examples/model-optimize/prompts/
  pi-root-cpu.md
  pi-source-worker.md
  pi-kernel-worker.md
```

`pi-root-cpu.md` should instruct the main agent to:

1. call `goal_plus_create`
2. record model-optimize triage
3. load the external CPU skill
4. copy or fill the CPU GoalContract
5. run baseline, correctness, benchmark, and profile
6. write the opportunity ledger
7. select one opportunity for `single` or `source-search`
8. launch child Search with Pi RPC workers
9. integrate selected child result into parent workspace
10. run root E2E verifier
11. report local and root scores

`pi-source-worker.md` should remind workers:

- optimize one opportunity only
- do not claim final root success
- respect allowed and denied files
- run `search_run_verifier` early
- prefer reversible patches and path-hit evidence

`pi-kernel-worker.md` should remind workers:

- produce a `KernelArtifact`
- preserve wrapper/reference/verifier contracts
- report guard coverage and integration risk
- leave final acceptance to root E2E

Acceptance:

- Prompts are explicit enough for Pi to run the CPU source-search flow.
- Prompt wording reinforces domain-skills-external and root-E2E-final-authority.

## 6. Phase 5: CPU End-To-End Walkthrough

Goal: run the first scenario manually through Pi.

Expected flow:

```text
/goal-plus <pi-root-cpu prompt>
  -> goal_plus_create
  -> goal_plus_record_triage
  -> baseline/correctness/profile
  -> opportunity ledger
  -> child source SearchSpec
  -> search_freeze_spec
  -> search_create
  -> goal_plus_link_search_run or scenario-local child link
  -> search_plan_next
  -> search_start_batch
  -> search_start_agent_session
  -> pi_rpc_run_worker
  -> search_bind_agent_handle
  -> final search_run_verifier
  -> search_select
  -> search_report
  -> integrate child result
  -> root_e2e verifier
  -> final scenario report
```

Files written during the run:

```text
.search/goal-plus/<goal_plus_id>/model-optimize/
  goal_contract.yaml
  opportunities.jsonl
  resource_ledger.json
  integration/
  report.md
```

Acceptance:

- At least three opportunities are recorded.
- At least one child source search runs with Pi RPC.
- Child search returns a selected candidate and report path.
- Parent integration runs root E2E.
- Final report distinguishes local child score from root E2E score.

## 7. Phase 6: Resource Ledger Prototype

Goal: model resource-constrained scheduling before using real NPU hardware.

Directory:

```text
examples/model-optimize/resource/
  lease.py
```

This helper is optional and scenario-local. It should not become a core Goal
Plus dependency yet.

Minimal behavior:

- read a resource manifest
- acquire a resource class lease with a file lock
- write allocation events to `resource_ledger.json`
- release resources after verifier completion
- support a mock 8-card NPU manifest

Example event:

```json
{
  "time": "2026-07-09T12:00:00Z",
  "event": "acquire",
  "owner": "run_xxx:c001",
  "resource_class": "e2e_4card",
  "devices": ["npu0", "npu1", "npu2", "npu3"]
}
```

Acceptance:

- Two mock child jobs receive non-overlapping device leases.
- Root E2E lease is exclusive.
- Ledger records acquire and release events.
- Design remains compatible with a future runtime resource API.

## 8. Phase 7: Kernel-Search Placeholder

Goal: exercise the kernel path without requiring real accelerator code.

Tasks:

1. Add a CPU local-op opportunity that is classified as kernel-like.
2. Extract a local contract:

   ```yaml
   inputs: [...]
   outputs: [...]
   correctness: [...]
   local_metric: avg_latency_ms
   integration_rule: source patch or wrapper call
   final_gate: root_e2e
   ```

3. Either defer it with a clear reason or run a child search against a CPU
   operator file.
4. Record a `KernelArtifact`-shaped result even if the artifact is CPU-only.

Acceptance:

- The root report shows kernel-search as one deepening mode, not a top-level
  completion authority.
- Local kernel-like score is not accepted unless root E2E improves.

## 9. Phase 8: Runtime Evolution Proposals

Goal: turn lessons from the CPU walkthrough into concrete Goal Plus changes.

Potential APIs:

```text
goal_plus_record_evidence(goal_plus_id, evidence)
goal_plus_record_opportunity(goal_plus_id, opportunity)
goal_plus_link_child_search(goal_plus_id, opportunity_id, run_id)
goal_plus_record_opportunity_decision(goal_plus_id, opportunity_id, decision)
goal_plus_record_resource_event(goal_plus_id, resource_event)
```

Potential Search runtime changes:

- git-worktree backend
- parent/child run graph metadata
- verifier budget counters across child runs
- resource request fields in `SearchSpec`
- worker status/heartbeat/observation APIs
- benchmark repetition and confidence summaries

Acceptance:

- Produce a short proposal or issue list after CPU walkthrough.
- Do not add runtime features before the scenario demonstrates the need.

## 10. Phase 9: Small Accelerator Fixture

Goal: move beyond CPU while avoiding vLLM complexity.

Candidate targets:

- single-GPU PyTorch toy model
- single-NPU CANNBench TileLang-Ascend example
- small local serving loop with one accelerator benchmark

Tasks:

1. Add an accelerator resource manifest.
2. Use the resource ledger for verifier commands.
3. Run one child source-search or kernel-search.
4. Keep root E2E workload small and repeatable.

Acceptance:

- Resource coordination prevents overlapping benchmark allocations.
- Local accelerator score and root E2E score are reported separately.
- No vLLM-specific logic is added to Goal Plus.

## 11. Phase 10: vLLM Scenario Pack

Goal: add vLLM as an external domain pack after the CPU and small accelerator
flows work.

Directory:

```text
examples/model-optimize/skills/
  vllm-model-opt/
    SKILL.md
```

The vLLM skill should define:

- how to run the selected benchmark
- how to run correctness/quality gates
- how to profile vLLM serving
- how to interpret scheduler, KV cache, backend routing, and kernel evidence
- allowed and denied edit surfaces
- how to recognize kernel-search opportunities
- how to require path-hit evidence

Initial vLLM target:

- small model
- small fixed workload
- source-search only
- no automatic kernel generation in the first vLLM run

Acceptance:

- vLLM skill is external and referenced by GoalContract.
- Root Pi agent discovers opportunities from evidence.
- At least one source-search child run completes.
- Root E2E verifier decides acceptance.

## 12. Phase 11: 8-Card NPU Campaign

Goal: test the full resource-constrained story.

Prerequisites:

- CPU fixture works.
- Small accelerator fixture works.
- Resource ledger works.
- vLLM source-search works.
- Verifier scripts support repeated benchmark and raw metrics preservation.

Tasks:

1. Fill `resource_manifest.npu8.yaml`.
2. Define resource classes for smoke, 1-card, 4-card, and 8-card E2E.
3. Add environment binding for the target NPU backend.
4. Limit concurrent child searches by resource class.
5. Serialize top-level E2E.
6. Record all resource events in the final report.

Acceptance:

- Two subagents do not use the same NPU slice at the same time.
- Baseline/champion/candidate comparisons use equivalent resource classes.
- Resource contention is visible in reports.
- Failures due to unavailable hardware are classified separately from
  correctness or performance failures.

## 13. Test And Verification Checklist

Run after each phase:

```bash
git diff --check -- examples/model-optimize
```

When runnable files exist:

```bash
python examples/model-optimize/cpu-toy/target/correctness.py
python examples/model-optimize/cpu-toy/target/benchmark.py
python examples/model-optimize/cpu-toy/target/profile.py
```

When SearchSpec templates exist:

```bash
python -m json.tool examples/model-optimize/templates/source_search_spec.cpu.json
python -m json.tool examples/model-optimize/templates/kernel_search_spec.placeholder.json
```

When scenario-local helper scripts exist:

```bash
python -m pytest tests/test_model_optimize_example.py -q
```

## 14. Exit Criteria For The First Milestone

The first milestone is complete when:

- `design.md` and `plan.md` are present.
- CPU fixture exists and is runnable locally.
- CPU domain skill exists and is referenced externally.
- CPU GoalContract and child SearchSpec templates exist.
- Pi root prompt and Pi worker prompts exist.
- A manual Pi walkthrough records opportunities and runs one child source
  search.
- Root E2E verifier accepts or rejects the integrated child result.
- Final report clearly separates:
  - opportunity evidence
  - child local search score
  - root E2E score
  - resource usage
  - accepted, rejected, and deferred work

