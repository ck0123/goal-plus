# Model Optimize Scenario Design

> Status: draft
> Scope: Pi-first, single-level Goal Plus flow for optimizing a real local
> model workspace.

## 1. Goal

This scenario starts from the same input a user should be able to provide:

```text
/goal-plus optimize this model workspace for this metric under this verifier
```

Goal Plus should inspect the workspace, discover the correctness command,
benchmark metric, edit surface, and optimization opportunities, then decide
whether the task is ready for Search Mode.

The scenario is intentionally not a recursive orchestration framework. Goal
Plus owns one top-level goal record. Search Mode owns one bounded verifier-backed
search run when the main agent has enough evidence to freeze a `SearchSpec`.
Model-optimization state such as opportunity notes, resource evidence, and final
harness results stays scenario-local.

## 2. Non-Goals

Do not add these to Goal Plus for this scenario:

- nested or recursive search scheduling
- first-class parent/child run graphs
- runtime-owned GPU, NPU, or CPU-card allocation
- worker heartbeat, wait, abort, observation, or lifecycle APIs
- built-in vLLM, CANN, profiler, kernel, or hardware topology concepts
- static SearchSpec templates for this example

The current goal is to prove that a plain prompt plus a real workspace can make
GP inspect, measure, and open Search only when the objective is bounded.

## 3. Runnable Target

The first runnable target is:

```text
examples/model-optimize/torch-cpu-target/
```

It is a deterministic PyTorch CPU workspace. Every runnable script forces a
single CPU core/thread budget through environment variables and `torch` thread
settings. Raising the thread count is not a valid optimization.

The target includes:

- `verify.py`: deterministic correctness check
- `benchmark.py`: emits `tokens_per_second` and `latency_ms` JSON metrics
- `profile.py`: emits simple opportunity evidence
- `model.py`: contains a fusible vector tail
- `serving.py`: contains redundant source-level work
- `cpp_reference/fused_vector_tail.cpp`: a checked C++ CPU operator pattern

This is not a fake string-manipulation fixture. It runs PyTorch code and gives
the main agent real files to inspect and real commands to execute.

## 4. Top-Level Flow

Expected Pi flow:

```text
User prompt
  -> /goal-plus
  -> goal_plus_create
  -> triage as measurable model optimization
  -> inspect target README, code, verifier, benchmark, profile output, and skill
  -> run baseline verify/benchmark/profile
  -> decide whether a bounded SearchSpec is ready
  -> either make one direct safe patch or open one Search run
  -> Search candidates run verifier-backed experiments in isolated workspaces
  -> select one candidate by metric and correctness evidence
  -> integrate selected result into the target workspace or report no safe win
  -> rerun verify.py and benchmark.py on the integrated target
  -> final audit against the original /goal-plus request
```

Search may still explore multiple candidates and workspaces. The restriction is
that GP should not become a nested search supervisor. One top-level goal can
upgrade one bounded optimization task into normal Search Mode.

## 5. Search Readiness

The main agent should open Search only after it can identify:

- objective: improve `tokens_per_second`
- direction: maximize
- correctness gate: `python verify.py`
- ranking metric: `python benchmark.py`
- target path: `examples/model-optimize/torch-cpu-target`
- allowed edit surface: model/source implementation files, not workload or
  verifier files
- resource budget: exactly one CPU core/thread
- candidate comparison rule: valid candidates must pass correctness and keep
  the single-thread budget

If these are not established, GP should keep analyzing or stop with a clear
blocker instead of inventing a SearchSpec.

## 6. Domain Guidance Boundary

Domain knowledge lives outside Goal Plus:

The example prompt and workspace docs may explain:

- how to read the target verifier, benchmark, and profile scripts
- what the single CPU core constraint means
- where the safe edit surfaces are
- when a C++ CPU operator is reasonable
- how `cpp_reference/fused_vector_tail.cpp` avoids changing the workload or
  verifier contract

Do not create an additional Pi skill for this scenario. Pi exposes `goal-plus`
as the complete skill entrypoint; scenario guidance must not define Goal Plus
lifecycle semantics, Pi RPC internals, or Search MCP state transitions.

## 7. Optimization Opportunities

The first target deliberately contains two useful opportunities:

| ID | Location | Expected action |
|---|---|---|
| `fuse_vector_tail` | `model.py` vector tail | try a smaller/fused implementation, optionally using the C++ reference pattern |
| `remove_redundant_projection` | `serving.py` | remove unused work if correctness and metric remain valid |

The important behavior to observe is not whether GP picks the exact seeded
answer. The important behavior is whether it uses workspace evidence to find a
bounded optimization and then measures the result.

## 8. Resource Boundary

For this example the resource policy is strict:

```text
CPU cores/threads: 1
GPU/NPU: not allowed
```

Future multi-card GPU/NPU scenarios should still keep allocation outside Goal
Plus core. A main agent, scenario helper, strategy, or external scheduler can
lease cards and write opaque resource evidence. Goal Plus may reference that
evidence in a final report, but it should not understand or schedule hardware
itself.

## 9. Evidence And Reporting

The final report should include:

- baseline verifier and benchmark output
- opportunity evidence from `profile.py` and code inspection
- whether GP opened Search Mode
- selected candidate evidence if Search ran
- final verifier and benchmark output
- confirmation that `torch_num_threads` stayed at `1`
- accepted changes, rejected changes, and remaining optimization ideas

## 10. Later Targets

After the single-core Torch target works, larger model-serving targets can be
added as external skills and workspaces:

- single-GPU or single-NPU PyTorch fixture
- small local vLLM serving workload
- resource-coordinated NPU benchmark

Those should stress the same GP boundary: domain knowledge and resource
allocation remain external; GP upgrades to Search only for bounded measurable
tasks.
