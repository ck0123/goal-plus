# Goal Plus Bounded Optimization Boundary

## Purpose

This document keeps Goal Plus small while recording the generic gaps exposed by
the `examples/model-optimize/` scenario.

The model-optimize example should not push Goal Plus into a nested
orchestration framework. Goal Plus remains a single top-level goal state
machine that can upgrade a bounded measurable task into Search Mode and audit
the original goal afterward.

## Scope Decision

Goal Plus should not support nested search orchestration.

Allowed:

- One top-level Goal Plus record for the user's goal.
- A bounded Search Mode upgrade when a verifier-backed `SearchSpec` is ready.
- Scenario-local files that record model-optimize opportunities, resource
  allocation, integration notes, and root harness results.
- Opaque evidence records or report links when a scenario needs to preserve
  audit material.

Not allowed for now:

- A first-class Goal Plus dependency graph.
- First-class multi-level child Search scheduling.
- Worker wait, abort, heartbeat, observation, or lifecycle supervision APIs.
- Runtime-owned GPU/NPU/card scheduling.
- Domain-specific concepts such as vLLM, CANN, kernels, profilers, or hardware
  topology in Goal Plus models.

## What GP Is Missing Now

The missing pieces are smaller than a full orchestration framework:

1. **Root harness evidence.** Goal Plus can record a linked Search result, but
   it does not have a narrow convention for attaching the final root verifier
   evidence that proves the original goal after integration.
2. **Opaque resource evidence.** Resource-heavy scenarios need to preserve
   which resource slot was assigned to which verifier or worker. Goal Plus does
   not need to schedule those resources, but final reports should be able to
   point at the evidence.
3. **Scenario harness guidance.** Tests and prompts need a clear boundary:
   scenario-local harnesses can run root E2E checks, while Search Mode
   verifiers remain local ranking evidence.
4. **Read-only reporting.** The monitor/report path can summarize scenario
   evidence links, time/cost, and root harness outcome without becoming a live
   controller.

These are audit and harness gaps, not orchestration gaps.

## Multi-Card Allocation Boundary

Multi-card allocation should stay outside Goal Plus core.

The main agent, scenario helper, strategy code, or an external scheduler may:

- read a scenario resource manifest
- acquire a resource lease
- set environment variables for verifier commands or worker launches
- release the lease
- write allocation events to a scenario-local ledger

Goal Plus may reference that ledger in final evidence. It should not interpret
the resource type or decide which cards are available.

Recommended scenario-local event shape:

```json
{
  "time": "2026-07-09T12:00:00Z",
  "event": "acquire",
  "owner": "goal_abc:run_xyz:c001",
  "resource_class": "exclusive_e2e",
  "resource_ids": ["slot0", "slot1"]
}
```

The names `slot0` and `exclusive_e2e` are scenario data. They may represent
CPU workers, GPU cards, NPU cards, ports, dataset shards, or any other
constrained resource. Goal Plus treats them as opaque evidence.

## Harness Boundary

Search Mode verifiers rank candidate workspaces. They are not automatically the
final authority for the original Goal Plus goal.

For model-optimize and similar scenarios, use a scenario-local root harness:

- `baseline_command`: optional baseline metric or correctness capture
- `root_correctness_command`: final correctness check after integration
- `root_metric_command`: final metric JSON after integration
- `promotion_rule`: KEEP/REVERT decision rule
- `artifact_policy`: paths to preserve for audit

Goal Plus can point to the root harness report through existing final audit
evidence or a future narrow evidence field. It does not need to own a generic
harness runner until more than one scenario repeats the same shape.

## Recommended Near-Term Shape

For `examples/model-optimize/`, keep the extra state scenario-local:

```text
.gp/goal-plus/<goal_plus_id>/model-optimize/
  opportunities.jsonl
  resource_ledger.json
  integration/
  root_harness.json
  report.md
```

Use existing Search Mode fields for the local run:

- `SearchSpec.constraints.goal_plus_id`
- `SearchSpec.constraints.opportunity_id`
- `SearchSpec.constraints.domain_skill_refs`
- `SearchSpec.constraints.final_gate_required`
- `SearchSpec.constraints.resource_request`

This is a convention for one scenario, not a new Goal Plus control plane.

## Possible Minimal GP Additions Later

Only after the scenario proves repeated need, consider small generic additions:

- `goal_plus_record_evidence(goal_plus_id, evidence)` for opaque audit records.
- A final-audit evidence field on `GoalPlusRecord`.
- Monitor/report rendering for evidence links and root harness summaries.

Avoid adding APIs for dependency graphs, nested scheduling, or multi-run
orchestration until a separate design explicitly justifies that complexity.
