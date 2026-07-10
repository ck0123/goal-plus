# Kernel Optimize Example Template

Run an agentic search to optimize an operator kernel against a PyTorch
reference, with `avg_latency_ms` as the primary metric. The runtime, the
`AnySearchAgent` autoresearch loop, and the `search` skill are unchanged;
this example only contributes a verifier reference and a usage guide.

## Inputs

The host agent (or the user) supplies two files:

1. **Reference** — pure PyTorch, exposing `Model`, `get_init_inputs()`, and
   either `get_inputs()` (single shape) or `get_input_groups()` (multi-shape).
2. **Kernel** — the file the candidate will edit. It must expose `ModelNew`.
   Supported DSLs: `cpp` (CPU) or any NPU-side DSL (`triton_ascend`,
   `ascendc`, `ascendc_catlass`, `pypto`, `tilelang_ascend`). DSL differences
   live inside `ModelNew.forward`; the verifier never inspects them.

## Step 1 — bootstrap verifier

Copy these three files from this directory into `<source>/_verifier/`:

- `verifier/verify.py`
- `verifier/benchmark.py`
- `verifier/_common_utils.py`

`<source>/` is the directory you will pass as `source_path` in the SearchSpec.
The runtime copies it into each candidate workspace, so `_verifier/` will be
present in every candidate automatically.

## Step 2 — build verify inputs

Under `_verifier/`, materialize two files matching the verifier's naming
convention:

- `{op_name}_torch.py` — a literal copy of the reference file
- `{op_name}_impl.py` — a literal copy of the kernel file

For most cases both are byte-for-byte copies of the user's inputs with the
filename changed. If the user's kernel file does not already define
`ModelNew` (e.g., a C++ project with the wrapper in a sibling file), assemble
a single `{op_name}_impl.py` that imports the binding and defines `ModelNew`.

## Step 3 — fill the SearchSpec

```json
{
  "objective": "minimize latency of <op> kernel",
  "metric_name": "avg_latency_ms",
  "metric_direction": "minimize",
  "source_path": "<source>",
  "edit_surface": {
    "allow": ["<source-relative kernel path>"],
    "deny": ["_verifier/", "<source-relative reference path>"]
  },
  "process_verifiers": [
    {
      "name": "correctness",
      "role": "process_gate",
      "command": ["python", "_verifier/verify.py", "--op_name", "<op>", "--verify_dir", "_verifier", "--impl_name", "impl"],
      "timeout_seconds": 600
    },
    {
      "name": "latency",
      "role": "ranking_signal",
      "command": ["python", "_verifier/benchmark.py", "--op_name", "<op>", "--verify_dir", "_verifier", "--impl_name", "impl", "--warmup", "5", "--repeats", "50", "--output", "_verifier/perf_result.json"],
      "timeout_seconds": 900
    }
  ],
  "promotion_verifiers": [
    {
      "name": "frozen_hash_gate",
      "role": "anti_cheat_gate",
      "command": ["search-runtime-internal", "check-frozen-hashes"]
    }
  ],
  "budget": {"max_candidates": 4, "max_parallel": 2},
  "strategy": {
    "name": "agent_guided",
    "driver": "builtin",
    "worker_mode": "agent-session-pool",
    "worker_agent_type": "AnySearchAgentDeep",
    "history_policy": {"scope": "top_n", "top_n": 5}
  }
}
```

Notes:

- `metric_name = avg_latency_ms` becomes the column-2 header of every
  candidate's `results.tsv`. The subagent minimizes this value.
- The latency verifier's stdout JSON is what the runtime parses into
  `aggregate_score`. `benchmark.py`'s `--output` should be set so its JSON
  lands at a stable path.
- `edit_surface.deny` must include `_verifier/` and the reference file so the
  candidate cannot tamper with the verifier or the ground truth.
- `worker_agent_type` is typically `AnySearchAgentDeep` (100 steps) for kernel
  optimization. Drop to `AnySearchAgent` (50) for quick smoke runs.

## Step 4 — drive the search flow

Hand off to the standard `search` skill. Full mechanics live in
`.opencode/skills/search/SKILL.md` and `examples/README.md`; this example
does not redefine them. The shape:

1. `search_freeze_spec(spec=<above>, verifier_artifact_paths=[`
   `"_verifier/verify.py", "_verifier/benchmark.py", "_verifier/_common_utils.py",`
   `"_verifier/<op>_torch.py", "_verifier/<op>_impl.py"])`
2. `search_create(frozen_spec_id=<id>)` — record `run_id`.
3. `search_plan_next(run_id, requested_k=4)`.
4. `search_start_batch(run_id, plan_id, proposals=[...])` — author 4
   proposals referencing the official history (first batch has empty
   `must_reference_one_of`, so proposals may start from source).
5. For each candidate: `search_start_agent_session` → launch `Task` with the
   `launch` payload → `search_bind_opencode_session` → `search_run_verifier
   (run_id, candidate_id, "process")` (no `agent_session_id`, final confirm).
6. `search_list_history`, `search_select`, `search_report`.
7. Optional: `search_promote(run_id, selected_candidate_id)`.

## Step 5 — subagent contract (unchanged)

The subagent runs the existing `AnySearchAgent` autoresearch loop:

1. `search_get_agent_context(agent_session_id)` → reads `workspace`,
   `allowed_files`, `denied_files`, `metric_name`, `metric_direction`,
   `history`, `iterations`.
2. Edits the kernel file inside `workspace/`, commits via git.
3. `search_run_verifier(..., agent_session_id=...)` → runtime invokes
   `_verifier/verify.py` (correctness gate) and `_verifier/benchmark.py`
   (latency). The latency JSON feeds `aggregate_score`.
4. Keep / discard / git-reset based on the score trajectory.

The subagent has no special kernel knowledge beyond what the spec and the
`_verifier/` scripts already say.

## DSL notes

- **`cpp`** — `ModelNew` typically wraps a custom op registered via
  `torch.utils.cpp_extension.load_inline` or a prebuilt `.so`. The kernel
  file the candidate edits is the `.cpp` source; the loader code is part of
  the denied wrapper.
- **`triton_ascend`** — `ModelNew.forward` calls a `@triton.jit` kernel with
  the appropriate grid.
- **`ascendc` / `ascendc_catlass`** — `ModelNew` calls into a project-built
  binding. The candidate edits operator source files inside the kernel
  project; build glue is denied.
- **`pypto` / `tilelang_ascend`** — `ModelNew` imports the DSL module
  directly.

The verifier is identical across these DSLs. Only the kernel file's contents
differ.

## Anti-cheat

The runtime's existing mechanism does all of it:

- `_verifier/` is denied in `edit_surface.deny`. Edits to it fail the
  `anti_cheat_gate` promotion verifier.
- The verifier files and the `{op}_torch.py` / `{op}_impl.py` files are
  passed as `verifier_artifact_paths` to `search_freeze_spec`, so their
  hashes are locked at freeze time.
- `benchmark.py`'s L1 verify gate ensures the candidate cannot get a latency
  score without first passing correctness.

## Out of scope

- Device management. If the kernel needs an NPU, the host agent ensures
  `import torch_npu` works in the source workspace before freeze.
- DSL-specific scaffolding. The host agent reads the kernel file to figure
  out imports.
- A new subagent. `AnySearchAgent` already autoresearches.

## Worked prompt

See [`example-prompt-cpp.md`](example-prompt-cpp.md) for a complete prompt the
host agent can paste into the search skill for a C++ kernel optimization
task.
