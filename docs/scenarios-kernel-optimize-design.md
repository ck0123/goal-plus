# Scenarios: kernel-optimize

## Status

Draft, 2026-07-03.

## Objective

Add a `scenarios/kernel-optimize/` directory to `agentic-any-search-mcp` as the first domain scenario bundle. The bundle lets a host agent run kernel optimization tasks (any DSL) through `/goal-plus` and the existing internal Search Mode engine without modifying runtime code, the `AnySearchAgent` subagent, or the internal `search` skill.

The scenario contributes three things and nothing else:

1. A short README that tells the host agent how to use the bundle.
2. A DSL-agnostic verifier (correctness + latency) implemented in pure PyTorch.
3. A worked example prompt using C++ as the DSL.

## Background

Three reference projects were reviewed:

- **`agentic-any-search-mcp` (this repo).** `/goal-plus` goal entrypoint plus generic Search MCP runtime. `SearchSpec` (objective / metric / source_path / edit_surface / verifier command / budget / strategy), isolated candidate workspaces, `AnySearchAgent` already runs an autoresearch loop (`hypothesis → edit → search_run_verifier → keep/discard/git`), runtime parses the last JSON object from the verifier command's stdout. Examples (`k_module`, `circle_packing`, `signal_processing`, `swe_bench_20212`) all freeze a small toy evaluator as the verifier artifact.
- **`akg/akg_agents/workspace_autoresearch`.** Claude-Code slash command workflow for kernel optimization. `task.yaml` (backend / framework / dsl / editable_files), state machine (BASELINE → PLAN → EDIT → eval → KEEP/DISCARD → FINISH), `KernelVerifier` shared across local and remote workers, CodeChecker to catch cheating, DSL adapters for `triton_ascend`, `ascendc`, `ascendc_catlass`, `pypto`, `tilelang_ascend`, `cpp`.
- **`cannbot-skills`.** Skill library. `ops/triton-op-verifier/scripts/{verify.py, benchmark.py, validate_triton_impl.py}` is the canonical Triton-Ascend verifier (multi-shape, dtype-threshold MERE/MARE, geometric-mean speedup, L1 verify gate). `plugins-official/triton-op-generator/AGENTS.md` is the 6-phase orchestration prompt.

The kernel-agent capability the user wants already exists in `akg` and `cannbot-skills`. The point of this scenario bundle is **not** to re-implement it. The point is to show that the existing `agentic-any-search-mcp` runtime is already sufficient to host that workflow, and to provide the minimum verifier reference so the host agent can bootstrap a kernel optimization run without leaving this repo.

## Design Principles

1. **Zero runtime change.** The scenario bundle should not require changes to `src/`, `AnySearchAgent.md`, the `/goal-plus` assets, or the internal `search` skill. If the design forces a runtime change, the design is wrong.
2. **Verifier is DSL-agnostic.** Both sides of the comparison are PyTorch `nn.Module`: `Model` (reference) and `ModelNew` (the candidate kernel wrapper). Whether `ModelNew` internally launches a Triton kernel, calls a C++ custom op, or invokes AscendC is invisible to the verifier.
3. **Host agent owns verifier generation.** The host agent reads the scenario README, copies the reference verifier into `<source>/_verifier/`, and points the `SearchSpec.process_verifiers` at it before `search_freeze_spec`. The subagent never writes verifier code — anti-cheat is enforced by the existing frozen-verifier mechanism.
4. **No new abstraction layer.** No builder, no template engine, no DSL adapter. The verifier reference is a readable Python file that the host agent may copy verbatim or adapt.
5. **The scenario is a guide, not a fork.** It does not duplicate the `akg` state machine or the `cannbot-skills` 6-phase orchestration. Those exist elsewhere; this scenario stays inside the Search MCP flow.

## Directory Layout

```text
agentic-any-search-mcp/
  scenarios/                              # new top-level directory
    README.md                             # scenario index
    kernel-optimize/
      README.md                           # scenario guide for the host agent
      verifier/
        verify.py                         # DSL-agnostic correctness verifier
        benchmark.py                      # DSL-agnostic latency benchmark
        _common_utils.py                  # shared dtype thresholds, shape runner
      example-prompt-cpp.md               # worked prompt, C++ DSL
```

No other files in the repository change.

## File Responsibilities

### `scenarios/README.md`

Three to ten lines. State that `scenarios/` is a domain-bundle collection that does not modify the runtime, list the bundles, point at `kernel-optimize/README.md`. Future bundles (`rag-system-build`, `agent-eval`, etc.) would land here.

### `scenarios/kernel-optimize/README.md`

The host agent's entry point. Structure:

1. **What this scenario is for.** Iterative optimization of a kernel whose correctness can be checked against a PyTorch reference, with latency as the primary metric.
2. **Inputs.**
   - A reference file exposing `Model`, `get_init_inputs()`, and `get_inputs()` or `get_input_groups()`. Pure PyTorch.
   - A kernel file exposing `ModelNew`. Any of the supported DSLs: `cpp` (CPU), `triton_ascend`, `ascendc`, `ascendc_catlass`, `pypto`, `tilelang_ascend` (NPU). DSL differences live inside `ModelNew.forward`; the verifier itself does not care.
3. **Step 1 — bootstrap verifier.** Copy `scenarios/kernel-optimize/verifier/{verify.py, benchmark.py, _common_utils.py}` into `<source>/_verifier/`. The host agent does this once per run.
4. **Step 2 — build verify inputs.** Under `_verifier/`, materialize `{op_name}_torch.py` (a literal copy of the reference file) and `{op_name}_impl.py` (a literal copy of the kernel file). Names must match the conventions `verify.py` and `benchmark.py` expect.
5. **Step 3 — fill `SearchSpec`.**
   - `objective`: short string describing the optimization target.
   - `metric_name`: `avg_latency_ms`.
   - `metric_direction`: `minimize`.
   - `source_path`: the working directory that contains `_verifier/` and the kernel file.
   - `edit_surface.allow`: `[<kernel file path>]`.
   - `edit_surface.deny`: `["_verifier/"]` plus any frozen reference file.
   - `process_verifiers`: two entries — `verify.py` (correctness gate) and `benchmark.py` (latency). The latency verifier's stdout JSON is what feeds the runtime's ranking.
   - `promotion_verifiers`: optional, the runtime's built-in frozen-hash checker.
   - `budget`: per run.
   - `strategy`: typically `agent_guided` with `worker_agent_type` `AnySearchAgentDeep` (100 steps) or `AnySearchAgent` (50 steps).
6. **Step 4 — drive the standard Goal Plus/Search Mode flow.** `/goal-plus` records triage and verifier confirmation, then Search Mode runs `freeze_spec → create → plan_next → start_batch → start_agent_session → Task → bind_opencode_session → (subagent autoresearch loop) → run_verifier → select → report → promote`. The README does not restate the full flow; it points at `.opencode/skills/goal-plus/SKILL.md`, `.opencode/skills/search/SKILL.md`, and `examples/README.md`.
7. **DSL notes.** One short paragraph per family: `cpp` wraps a custom op via `torch.utils.cpp_extension` or a prebuilt `.so`; `triton_ascend` imports a `@triton.jit` kernel; `ascendc` / `ascendc_catlass` typically build a project and expose a torch binding; `pypto` / `tilelang_ascend` import their DSL module. The note is informational — the host agent reads the kernel file to confirm the import path. The verifier stays identical across all families.

### `scenarios/kernel-optimize/verifier/verify.py`

Adapted from `cannbot-skills/ops/triton-op-verifier/scripts/verify.py`, with these changes:

- Drop `--triton_impl_name`; replace with `--impl_name`. Default `impl`.
- Keep `{verify_dir}/{op_name}_torch.py` and `{verify_dir}/{op_name}_{impl_name}.py` file conventions.
- Keep multi-shape semantics: each shape in its own `try/except`, all shapes run before the result is flushed.
- Keep dtype-threshold MERE/MARE judgment (delegates to `_common_utils.py`).
- Keep Strategy A exit code: `passed_cases == total_cases` → 0; otherwise 1.
- Output JSON shape unchanged: `{op_name, total_cases, passed_cases, failed_cases, failures[]}`.
- No DSL-specific imports. No `triton`, no `torch_npu`. Just `torch`, `json`, `argparse`, `importlib.util`.

CLI:

```text
python3 verify.py --op_name <op> --verify_dir <dir> [--impl_name impl] [--timeout 900] [--output <path>]
```

### `scenarios/kernel-optimize/verifier/benchmark.py`

Adapted from `cannbot-skills/ops/triton-op-verifier/scripts/benchmark.py`:

- Same `--impl_name` rename.
- L1 verify gate stays: read `{verify_dir}/verify_result.json` (or `{verify_dir}/verify_result_{impl_name}.json` for non-default impl), if `passed_cases < total_cases` exit 2.
- Latency measurement covers two backends:
  - **NPU** — when `torch.npu` is available, use `torch.npu.synchronize()` around the timed region and a warmup loop before measurement.
  - **CPU** — fallback, using `time.perf_counter()` with `torch.set_num_threads(...)` left at the process default.
- Output JSON: `{framework: {avg_latency_ms, peak_memory_mb}, implementation: {avg_latency_ms, peak_memory_mb}, speedup_vs_torch, per_shape_results[], nan_indices, inf_indices, zero_indices, negative_indices, none_indices}`.
- `speedup_vs_torch` is the geometric mean of per-shape `framework_latency / impl_latency` over finite positive values; null if all shapes are anomalous.

CLI:

```text
python3 benchmark.py --op_name <op> --verify_dir <dir> [--impl_name impl] [--warmup 5] [--repeats 50] [--output <path>] [--verify_not_required]
```

### `scenarios/kernel-optimize/verifier/_common_utils.py`

Shared helpers:

- `dtype_threshold(dtype)` → threshold table for `float16`, `bfloat16`, `float32`, `hifloat32`, `float8_e4m3`, `float8_e5m2` (fallback `float32`).
- `compare_tensors(actual, golden, dtype)` → `(passed, mere, mare)`. Uses MERE/MARE dual gate: `MERE < threshold` and `MARE < 10 * threshold` over finite mask. Bool dtype uses `torch.equal`. NaN/Inf positions must match exactly.
- `run_shapes(model_init_inputs, input_groups_or_inputs, fn)` → list of `(case_idx, status, error_type, error_msg)`. The caller decides what to do with failures.
- `geomean(values)` → float or None.

### `scenarios/kernel-optimize/example-prompt-cpp.md`

A prompt the user can paste into `opencode run --command goal-plus "..."`.
Same style as `examples/README.md`'s circle_packing prompt blocks. Concrete
contents:

- State the task: optimize a C++ kernel exposed via `torch.utils.cpp_extension`. Reference is `workspace/matmul_ref.py` (a torch `Model`). Kernel under work is `workspace/matmul_kernel.cpp` plus `workspace/matmul_wrapper.py` (defines `ModelNew`, loads the `.cpp` via `load_inline`).
- Direct the host agent through Steps 1–4 of the README (bootstrap verifier, build `_verifier/matmul_torch.py` and `_verifier/matmul_impl.py`, fill the spec).
- Include the literal `SearchSpec` JSON to freeze, with `process_verifiers` commands spelled out.
- Direct the host agent to request 4 candidates, two batches, `worker_agent_type = AnySearchAgentDeep`, `strategy = agent_guided`.
- Direct the host agent to follow `.opencode/skills/goal-plus/SKILL.md` first,
  then the internal `.opencode/skills/search/SKILL.md` after Search Mode starts,
  and to report `run_id`, all 4 candidate scores, `selected_candidate_id`, and
  `report.md` path at the end.
- Note: the C++ kernel file is the only entry in `edit_surface.allow`. The wrapper is denied (changing the binding changes the binding contract, not the kernel — that is out of scope for this scenario).

## Anti-Cheat

The runtime already enforces frozen verifier hashes via `promotion_verifiers` and rejects edits to denied files. The scenario leverages this:

- `_verifier/` directory is listed in `edit_surface.deny` of the `SearchSpec`. The subagent cannot modify the verifier.
- The verifier files are passed as `verifier_artifact_paths` to `search_freeze_spec`, so their hashes are locked at freeze time.
- The C++ wrapper (or any torch binding glue) is also denied — the candidate can only edit the kernel source itself. The runtime's existing `anti_cheat_gate` verifier catches violations.
- No new anti-cheat mechanism is added.

## Host Agent Flow

```text
1. User asks for kernel optimization.
2. Goal Plus records triage and recognizes the kernel-optimize scenario.
3. Host agent copies verifier/* into <source>/_verifier/.
4. Host agent materializes {op}_torch.py and {op}_impl.py under _verifier/.
5. Host agent fills a SearchSpec following the README's Step 3.
6. If this was Initial Search-Ready, Goal Plus confirms the frozen verifier with the user.
7. search_freeze_spec(spec, verifier_artifact_paths=[_verifier/verify.py, _verifier/benchmark.py, _verifier/_common_utils.py, <op>_torch.py, <op>_impl.py])
8. search_create → search_plan_next → search_start_batch → start_agent_session → Task → bind → (AnySearchAgent autoresearch loop) → search_run_verifier → select → report → promote
```

Step 2 ("host agent recognizes the scenario") relies on the existing OpenCode skill description match. Either:

- The user references the scenario explicitly (`"use the kernel-optimize scenario to optimize <this kernel>"`), or
- The host agent loads `scenarios/kernel-optimize/README.md` itself when it sees a kernel optimization task.

The scenario does not register a new skill with OpenCode. The README is a markdown document the host agent reads on demand.

## Out Of Scope

- **DSL adapters.** No per-DSL scaffold / build logic. The host agent reads the kernel file and figures out imports.
- **Remote workers.** Device management (NPU) is the host agent's responsibility, not the scenario's. If a kernel needs an NPU, the host agent ensures `import torch_npu` works in the candidate workspace before freeze.
- **Multi-DSL subdirectories under `verifier/`.** There is one verifier, shared across all DSLs. If a future DSL genuinely needs different judgment (e.g., AscendC-specific crash triage), it gets its own bundle under `scenarios/`, not a fork of this one.
- **ST cases.** No new test under `tests/st/`. The verifier scripts can carry lightweight unit tests (assert dtype thresholds, assert `compare_tensors` on a known pair) under `tests/unit/` if desired, but this is not required for the first version.
- **A new subagent variant.** `AnySearchAgent` already autoresearches. `KernelSearchAgent` is not added.
- **Reuse of `akg` or `cannbot-skills` code at runtime.** The verifier is rewritten in this repo to keep it self-contained. The README mentions `cannbot-skills` and `akg` as upstream references for further reading.

## Risks And Open Questions

- **Will the host agent reliably generate `{op}_torch.py` and `{op}_impl.py` from arbitrary user inputs?** The README prescribes "literal copy" semantics. For most cases this works. Edge case: the user's reference file has a non-standard name or wraps the kernel in an unusual way. The README's "DSL notes" section is the mitigation — it lists the common shapes and tells the host agent to read the file. If this proves insufficient, a future scenario can add a tiny `bootstrap.py` helper. Out of scope for v1.
- **NPU vs CPU only.** `benchmark.py` ships two sync paths: `torch.npu.synchronize()` when `torch.npu` is importable, `time.perf_counter()` otherwise. Other backends are not supported. If a candidate kernel targets a backend the benchmark cannot time, the run will fall through to the CPU path and produce misleading numbers — the README's "DSL notes" calls this out so the host agent can refuse such inputs up front.
- **Should `verify.py` block on the kernel's first-time JIT/compile cost?** Yes — first-call compile is part of the candidate's correctness check. The README documents this. If a compile takes longer than `--timeout`, that is a candidate failure, which is the intended semantics.
- **Whether to commit a runnable fixture under `scenarios/kernel-optimize/example/`.** This design declines. The `example-prompt-cpp.md` is a prompt, not a fixture. If a future contributor wants a runnable smoke test, they should add it under `examples/` (the existing pattern), not under `scenarios/`.
- **Versioning.** As the runtime evolves (e.g., `process_verifiers` schema changes), the scenario README may need updates. There is no automated sync. The README references the runtime version it was written against.
