# Model Optimize Scenario Implementation Plan

> Status: draft
> Priority: Pi-first, real Torch CPU target, no static SearchSpec templates.

## 0. Commitments

- Keep Goal Plus generic. Do not add model-optimization logic to GP core.
- Do not add recursive or nested search orchestration.
- Do not add static SearchSpec templates for this scenario.
- Start from one real workspace plus one minimal `/goal-plus` prompt.
- Keep the entire target on exactly one CPU core/thread.
- Keep domain guidance in the example prompt and workspace docs, not in an
  additional Pi skill.
- Let GP inspect the workspace and decide whether Search Mode is justified.

The previous template/prompt-pack/CPU-toy skeleton items are removed from this
plan.

## 1. Torch CPU Target Workspace

Goal: provide a small but real PyTorch workspace that GP can inspect and
optimize.

Directory:

```text
examples/model-optimize/torch-cpu-target/
  README.md
  single_thread.py
  model.py
  serving.py
  workload.json
  verify.py
  benchmark.py
  profile.py
  cpp_reference/
    fused_vector_tail.cpp
    run_reference.py
```

Requirements:

- `verify.py` checks deterministic output and reports `torch_num_threads: 1`.
- `benchmark.py` emits final JSON with `tokens_per_second`, `latency_ms`,
  `valid`, and `torch_num_threads`.
- `profile.py` reports at least:
  - `fuse_vector_tail`
  - `remove_redundant_projection`
- `model.py` contains vector math that can be fused.
- `serving.py` contains redundant work that can be removed.
- Every script forces one CPU core/thread and rejects higher thread counts.

Acceptance:

```bash
python examples/model-optimize/torch-cpu-target/verify.py
python examples/model-optimize/torch-cpu-target/benchmark.py
python examples/model-optimize/torch-cpu-target/profile.py
```

Each command must complete locally and emit parseable JSON.

## 2. C++ CPU Operator Reference

Goal: make the example guidance concrete enough that a future worker is not
starting from a vague instruction.

Files:

```text
examples/model-optimize/torch-cpu-target/cpp_reference/
  fused_vector_tail.cpp
  run_reference.py
```

Requirements:

- The C++ file registers a `torch.ops` CPU operator with `TORCH_LIBRARY`.
- The operator implements the same fused vector tail as the Python baseline.
- The reference runner builds the extension with one build job and validates
  output against the Python implementation.
- The reference is guidance, not required production code for the final target.

Acceptance:

```bash
python examples/model-optimize/torch-cpu-target/cpp_reference/run_reference.py
```

If the local toolchain is missing, record the blocker. If it is available, the
runner should report `valid: true`.

## 3. Prompt-Local Domain Guidance

Goal: keep domain-specific optimization guidance outside Goal Plus core without
creating extra Pi skills.

The example prompt and workspace docs should explain:

- the single CPU core constraint
- how to run `verify.py`, `benchmark.py`, and `profile.py`
- safe edit surfaces
- forbidden edits, including workload, verifier, and thread-count changes
- when a C++ CPU operator is reasonable
- how to use `cpp_reference/fused_vector_tail.cpp` as a pattern

Acceptance:

- `.pi/skills/goal-plus/SKILL.md` remains the only Pi skill.
- The prompt/docs do not describe GP internals as implementation instructions.
- The prompt/docs do not hand-write a SearchSpec.
- A Pi main agent can use them as optional domain guidance.

## 4. Minimal User Prompt

Goal: model the real user entry point.

File:

```text
examples/model-optimize/pi-goal-prompt.md
```

The prompt should:

- invoke `/goal-plus`
- point at `examples/model-optimize/torch-cpu-target`
- optimize `tokens_per_second`
- require `verify.py` to remain valid
- forbid increasing CPU cores or thread counts
- allow C++ CPU vector fusion if useful
- ask GP to report whether Search Mode was opened

Acceptance:

- The prompt does not include a SearchSpec.
- The prompt does not list candidate patches.
- GP must inspect the workspace and evidence before searching.

## 5. Local Verification

Run focused checks after edits:

```bash
python -m pytest tests/test_model_optimize_torch_cpu_target.py -q
python examples/model-optimize/torch-cpu-target/verify.py
python examples/model-optimize/torch-cpu-target/benchmark.py
python examples/model-optimize/torch-cpu-target/profile.py
python examples/model-optimize/torch-cpu-target/cpp_reference/run_reference.py
git diff --check
```

Before claiming the scenario is ready, also run the repository default test
suite when time allows:

```bash
python -m pytest -q
```

## 6. Commit-Backed Search Selection

Goal: make each verifier iteration a recoverable code version, not just a
score row.

Requirements:

- Candidate workspaces are initialized as git repositories with a baseline
  commit.
- `search_run_verifier` automatically commits changed candidate artifact files
  before running the verifier.
- `IterationRecord` exposes the real `git_head`, artifact cleanliness, and git
  status through MCP, monitor snapshots, and reports.
- `search_select` ranks verifier-recorded iterations, checks out the best
  committed `git_head`, and runs a main-agent final verifier on that exact
  commit before recording selection.
- `search_promote` generates the patch from the selected commit, not from a
  later workspace state.

Acceptance:

```bash
python -m pytest tests/test_runtime_unit.py::test_run_verifier_records_real_git_commit_for_iteration -q
python -m pytest tests/test_runtime_unit.py::test_select_checks_out_best_git_commit_before_final_verify -q
```

The monitor output for a completed run should include `selected_git_head`,
`last_git_head`, and `best_iteration_git_head`.

## 7. Real Pi Goal Plus Attempt

Goal: try the actual Pi GP path from this checkout.

Expected command shape:

```bash
pi --approve --no-session \
  -p "$(cat examples/model-optimize/pi-goal-prompt.md)"
```

Use the local ignored Goal Plus/Search root:

```bash
AGENTIC_ANY_SEARCH_ROOT=.gp
AGENTIC_ANY_SEARCH_SOURCE_PATH="$PWD"
```

Acceptance:

- If Pi starts, record whether GP created a goal, inspected the workspace, and
  opened Search Mode.
- If Search runs, record selected candidate evidence, selected `git_head`, and
  final `tokens_per_second`.
- If Pi, credentials, or MCP wiring are unavailable, record the exact blocker
  and leave the local target verified.
- Stop any long-running session before finishing the turn.

## 8. Near-Term GP Gaps To Evaluate

Only after the real run, decide whether GP needs small generic improvements.
Current likely gaps are audit-level, not orchestration-level:

- better way to attach root verifier evidence to the final GP audit
- clearer convention for linking an opaque scenario report
- clearer resource evidence links for future GPU/NPU runs

Do not add GP APIs for nested scheduling, lifecycle supervision, or hardware
allocation based only on this first CPU scenario.

## 9. First Milestone Exit Criteria

The first milestone is complete when:

- old static scenario template directories are gone
- `torch-cpu-target` runs locally on one CPU thread
- C++ fused-op reference builds and validates, or the local toolchain blocker
  is recorded
- `.pi/skills/goal-plus/SKILL.md` is the only Pi skill
- `pi-goal-prompt.md` is the only user prompt artifact
- a real Pi GP attempt has been made
- final notes state whether GP opened Search, which `git_head` was selected,
  and what evidence was produced
