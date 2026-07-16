# AscendC Direct Invoke Goal-Driven Development Plan

Chinese version: [ascendc-direct-invoke-development-plan-zh.md](ascendc-direct-invoke-development-plan-zh.md)

## 1. Product Contract

The only user entrypoint is `/goal-plus`. A request contains:

- operator semantics and API intent;
- approximate shape and dtype requirements;
- reference hints such as CANNBench, AKG, PyTorch, documentation, or local
  code;
- an optimization objective when it differs from weighted latency.

The user does not create a task directory, verifier, Golden, case file,
SearchSpec, or platform manifest. Goal Plus produces those artifacts during
Spec Discovery and then runs the standard Search flow.

Example:

```text
/goal-plus Implement and optimize an AscendC Direct Invoke sigmoid operator.
Input x has rank 1 through 4, roughly 1 through 65536 elements, and supports
float16 and float32. Output has the same shape and dtype. Use torch.sigmoid for
semantics and the CANNBench sigmoid task for case and tolerance evidence.
```

Only Direct Invoke is in scope. Candidate implementation remains unconstrained
inside the frozen edit surface. No source-repository Agent, Plugin, hook,
approval flow, or nested orchestration is used.

## 2. Goal Plus Flow

```text
natural-language goal
  -> goal_plus_create
  -> triage: scenario=ascendc_direct_invoke, phase=spec_discovery
  -> normalize request and resolve references
  -> generate workspace, Golden, cases, verifier, baseline, and SearchSpec
  -> self-test verifier and freeze the complete contract
  -> standard Goal Plus Search candidates
  -> select verifier-backed Git revision
  -> promotion verification
  -> patch from the immutable selected commit
  -> raw-goal audit
```

This is a host-skill workflow over the domain-neutral Goal Plus Runtime. The
Runtime continues to see only source paths, edit surfaces, frozen artifacts,
commands, pass/fail results, finite metrics, Git revisions, and patches.

The normative workflow is
[`examples/ascendc-direct-search/SPEC_DISCOVERY.md`](../examples/ascendc-direct-search/SPEC_DISCOVERY.md).

## 3. Ownership

| Owner | Responsibilities |
|---|---|
| User | Describe semantics, approximate shapes/dtypes, and reference hints. |
| Main agent | Resolve evidence, generate and self-test the task contract, measure the baseline, freeze artifacts, and orchestrate Search. |
| Candidate worker | Implement and optimize only allowed AscendC files using the frozen task and read-only knowledge. |
| Goal Plus Runtime | Isolate candidates, execute verifiers, record scores, select a Git revision, report, and promote. |
| Promotion verifier | Rebuild and validate the selected revision with full acceptance coverage. |

## 4. Reference Resolution

Each reference is assigned one or more explicit roles:

- `semantics`: mathematical and API behavior;
- `golden`: executable correctness Oracle evidence;
- `cases`: shape, dtype, attribute, range, and weight evidence;
- `tolerances`: numerical acceptance evidence;
- `baseline`: measurable non-candidate implementation;
- `implementation`: implementation ideas only.

The main agent records repository URL, exact revision, selected paths, hashes,
roles, and transformations. CANNBench may provide Golden and case evidence;
AKG may provide implementation, tests, and semantic evidence; PyTorch may be
the Golden and baseline. No provider is trusted for roles its files do not
support.

The baseline does not need to be AscendC. A user-provided Golden or another
independent executable reference can be measured as the baseline. The
Candidate is never used as its own Oracle or baseline.

## 5. Generated Task Bundle

Spec Discovery creates a source-owned workspace containing:

```text
operator/                         # template-derived Direct Invoke sources
_task/operator_request.json       # normalized user contract
_task/reference_manifest.json     # pinned evidence and hashes
_task/target_platform.json        # SoC/CANN/torch/torch_npu identity
_task/search_policy.json          # cases, metric, measurement, promotion
_task/baseline.json               # measured reference performance
_task/verifier_readiness.json     # generated-checker self-tests
_oracle/reference.py              # generated or adapted independent Golden
_oracle/cases.jsonl               # stable IDs and provenance
_oracle/tolerances.json           # explicit per-dtype policy
_verifier/                        # generated task-specific entrypoints
_skills/                          # pinned read-only AKG-primary knowledge
```

There is no checked-in task preparer or universal AscendC verifier. The host
agent uses the checked-in request schema, source template, knowledge selection,
materializer, and normative guide to generate the concrete files for each goal.

## 6. Direct Invoke v1 Boundary

Before Search, reject a request that does not satisfy all current constraints:

- exactly one Tensor output;
- the first schema argument is a non-optional Tensor;
- output shape and dtype relations are unambiguous;
- a PyTorch NPU extension can expose the Direct Invoke entrypoint;
- all concrete supported dtypes and bounded representative shapes are known.

Broadcast, reduction, matmul, dynamic-output, or multi-output tasks require a
separately defined scaffold profile. They must not be forced into the v1
shape-preserving template.

## 7. Correctness And Performance Evidence

Every performance case must also pass Search precision. Precision evidence is
bound to:

- stable passed case IDs;
- the complete cases file SHA-256;
- the exact built candidate artifact hash.

Benchmarking rejects missing, stale, partial, or mismatched precision
evidence. Correctness checks cover output count, shape, dtype, device, finite
values beyond `atol`/`rtol`, integer mismatch where applicable, NaN positions,
Inf positions, and Inf signs.

The generated verifier must prove its own sensitivity with positive and
negative controls before baseline measurement. Search starts only after the
source workspace passes the ranking command with a finite metric. This source
workspace is a minimally correct Seed: the main agent must make it build and
pass every shared correctness case, but Candidate workers own performance
optimization. The non-Candidate Baseline is a separate comparison input.

Search and Promotion execute the same frozen acceptance contract: identical
correctness and performance case IDs, Oracle, tolerances, scoring metadata,
measurement protocol, aggregation, metric direction, and rejection thresholds.
Promotion independently repeats it from a clean build with fresh measurements;
it cannot introduce a new gating condition that Search did not enforce.

Metric precedence is explicit user scoring, then an executable scoring contract
from the selected reference, then the default. Reference scoring is adapted to
a finite metric and uses one frozen scorer and comparison basis for the
Baseline and every Candidate. Without user or reference scoring, every
correctness case must pass and the finite default metric is
`weighted_latency_us` with direction `minimize`.

## 8. Knowledge Boundary

`knowledge.sources.json` uses the curated AKG AscendC tree at commit
`a2c1a23fd371e234b7e767247e8c4753462ecdca` as the primary source. It expands
only `SKILL.md` and `reference/` or `references/` Markdown. The source path may
be the AKG checkout root or its nested
`akg_agents/python/akg_agents/op/resources/skills/ascendc` directory.

AKG currently covers Direct Invoke, common APIs and patterns, elementwise,
broadcast, reduction, performance optimization, and debugging. The selection
therefore uses CANNBot commit
`d5ddcacc6e51eeaa8b52fa446c3b768c6813602e` only as an explicit file allowlist
for uncovered architecture, matmul, Cube-Vector fusion, SIMT, attention, sort,
and conversion guidance. `materialize_knowledge.py` reads both sources from
their selected Git object databases, not from live working trees.

For each task, the materializer removes orchestration and unsafe instructions,
rewrites or rejects dependencies, preserves the Apache 2.0 and CANN OSL 2.0
licenses separately, and writes a manifest containing source roles, resolved
commits, source blobs, source hashes, rendered hashes, materializer hash, and
transformation audits. Only this generated read-only `_skills/` bundle reaches
Candidate workspaces. Frozen task and reference contracts take precedence; the
bundle cannot define semantics, cases, tolerances, scores, edit surfaces, or
workflow.

## 9. Environment

Environment-sensitive discovery and validation load:

```bash
source "${GOAL_PLUS_NPU_CONDA_SH:?set GOAL_PLUS_NPU_CONDA_SH}"
source "${GOAL_PLUS_NPU_ENV_SH:?set GOAL_PLUS_NPU_ENV_SH}"
```

Detected target facts are frozen in the generated task rather than supplied as
user-facing command-line arguments.

## 10. Acceptance Criteria

The scenario is ready when all of the following hold:

- all four host `goal-plus` skills route `ascendc_direct_invoke` to the
  normative guide;
- no task preparer, fixed AscendC verifier, or profile compatibility file is
  shipped;
- the source template, knowledge selection, materializer, and generated
  provenance are internally valid;
- the generated checker has all required negative controls;
- performance cases are precision-covered and evidence-bound;
- Search and Promotion enforce the same frozen acceptance and scoring contract;
- Candidate files are the only editable task artifacts;
- Promotion exports from the immutable selected Git commit;
- repository unit tests and `git diff --check` pass;
- a real `/goal-plus` NPU smoke is run when a concrete operator goal is
  supplied.
