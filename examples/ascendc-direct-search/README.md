# AscendC Direct Invoke Through `/goal-plus`

This scenario has one user-facing entry: a natural-language `/goal-plus` goal.
There is no task preparer for users to run and no bundled task-specific
verifier. During Spec Discovery, the Goal Plus main agent resolves the
references named by the user, creates the operator workspace, generates and
self-tests the verifier, freezes the resulting contract, and only then starts
Search candidates.

Curated AKG AscendC skills and explicit CANNBot gap supplements are generated
per task as read-only implementation knowledge. They do not provide an Agent,
Plugin, hook, approval flow, branch workflow, or verifier.

## Example Goal

```text
/goal-plus Implement and optimize the CANNBench Level1 GELU task as an AscendC
Direct Invoke operator. Resolve https://gitcode.com/cann/cann-bench.git at an
exact commit and use tasks/level1/gelu plus the repository's official evaluation
contract as the semantics, cases, tolerances, Golden, and scoring reference.
Run on the available Ascend NPU. Use max_candidates=2 and max_parallel=2, then
complete selection, promotion verification, and reporting.
```

The request may instead name an AKG implementation, another local repository,
a Python callable, or a set of files. A reference is evidence for one or more
roles; it is not automatically trusted for every role.

## Main-Agent Contract

After Goal Plus triage selects `spec_discovery` with scenario
`ascendc_direct_invoke`, the main agent must follow
[`SPEC_DISCOVERY.md`](SPEC_DISCOVERY.md). In particular, it must:

1. normalize the user request using [`request.schema.json`](request.schema.json);
2. resolve exact reference revisions and record file hashes;
3. derive the Direct Invoke scaffold from [`template/`](template/) and make a
   minimally correct Seed that passes the shared correctness contract;
4. run [`materialize_knowledge.py`](materialize_knowledge.py) with
   [`knowledge.sources.json`](knowledge.sources.json) to export sanitized
   AKG-primary knowledge and declared CANNBot supplements from exact Git
   commits into `_skills/`;
5. generate task-specific Golden, cases, correctness checks, benchmark, and
   promotion checks from the normalized request and resolved references;
6. self-test those checks, measure the reference baseline, and freeze every
   non-editable artifact before starting candidates;
7. use the ordinary Goal Plus Search flow for candidates, selection, reporting,
   and promotion.

The user is not required to supply a CANNBench task directory, a verifier, a
Meta input index, CANN version strings, or a SearchSpec. The main agent discovers
those facts from the request, references, repository, and target environment.

Search and Promotion freeze and execute one acceptance contract. They use the
same correctness cases, performance cases, Oracle, tolerances, scoring inputs,
metric, and thresholds; Promotion is an independent clean rerun, not a stricter
hidden contract. An explicit user metric takes precedence, followed by an
executable metric supplied by a selected reference. Without either, every
correctness case must pass and valid Candidates are ranked by minimum weighted
latency. A named reference's complete public case set is the default unless the
user explicitly requests and accepts a partial smoke scope.

The Seed and Baseline are different. The main agent must make the initial Seed
build and pass every shared correctness case before Candidate launch, but it may
be slow. The Baseline is only the consistently applied comparison implementation
or data used for ranking.

## Environment

For this workspace, NPU commands must load the target environment first:

```bash
source "${GOAL_PLUS_NPU_CONDA_SH:?set GOAL_PLUS_NPU_CONDA_SH}"
source "${GOAL_PLUS_NPU_ENV_SH:?set GOAL_PLUS_NPU_ENV_SH}"
```

Environment discovery is part of Spec Discovery. Detected SoC, CANN, torch,
and torch_npu facts are written into the generated task bundle and frozen; they
are not user-facing preparation arguments.

Generated verifiers treat the Candidate workspace as read-only. Each invocation
copies it into the unique `GOAL_PLUS_VERIFIER_TMPDIR`, builds and runs official
evaluation there, writes all wheels, shared objects, reports, and evidence there,
and emits the final metric or complete failure evidence on stdout. The generated
wrapper uses `goal_plus.verifier_support.isolated_verifier_workspace`, and may
write compact retained evidence to `GOAL_PLUS_VERIFIER_DIAGNOSTICS_DIR`. Search
and Promotion commands use the same target-NPU `resource_lock` and the same
frozen Baseline/scoring inputs.

## Real NPU Smoke

Run the opt-in CANNBench Level1 GELU flow with:

```bash
scripts/run_ascendc_cannbench_e2e.sh
```

The script installs the current Goal Plus checkout, loads the NPU environment,
and invokes Pi's native `/goal-plus` command. The main Pi host opens one fixed
pool to run two Pi RPC candidates concurrently (`max_candidates=2`,
`max_parallel=2`). The test requires full selection,
promotion evidence, immutable-revision patch generation, and a completed Goal
Plus record. It is gated by `GOAL_PLUS_RUN_ASCENDC_NPU_ST=1` and is not part of
ordinary unit or ST runs.

## Included Assets

```text
ascendc-direct-search/
  SPEC_DISCOVERY.md       # normative main-agent workflow
  request.schema.json     # normalized request written by the main agent
  template/               # pinned Direct Invoke source template
  knowledge.sources.json  # pinned AKG skill tree + CANNBot gap allowlist
  materialize_knowledge.py # Git-object export, sanitization, and provenance
```

`template/` is an input to Spec Discovery. The knowledge bundle is generated
per task from pinned AKG and CANNBot Git objects and then frozen. Candidate
workers receive only that generated `_skills/` bundle in their isolated
workspaces; they never execute instructions or scripts from either source
repository.
