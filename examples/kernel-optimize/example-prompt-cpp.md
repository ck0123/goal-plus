# Example prompt: C++ kernel optimization

A prompt the host agent can paste into the search skill. It assumes the
following layout under `<source>/`:

```text
<source>/
  matmul_ref.py          # torch Model + get_inputs
  matmul_kernel.cpp      # candidate-editable kernel source
  matmul_wrapper.py      # ModelNew wrapper, loads the .cpp via load_inline
```

The reference and wrapper are denied to the candidate. Only `matmul_kernel.cpp`
is in `edit_surface.allow`.

---

## Prompt

```text
Use the kernel-optimize example template to optimize the matmul operator at <source>/.

Step 1 — bootstrap verifier:
- Copy examples/kernel-optimize/verifier/{verify.py, benchmark.py, _common_utils.py}
  to <source>/_verifier/.

Step 2 — build verify inputs under <source>/_verifier/:
- Copy <source>/matmul_ref.py to <source>/_verifier/matmul_torch.py.
- Copy <source>/matmul_wrapper.py to <source>/_verifier/matmul_impl.py.
  (matmul_wrapper.py already defines ModelNew; renaming is sufficient.)

Step 3 — fill and freeze this SearchSpec:

{
  "objective": "minimize latency of matmul implementation",
  "metric_name": "avg_latency_ms",
  "metric_direction": "minimize",
  "source_path": "<source>",
  "edit_surface": {
    "allow": ["matmul_kernel.cpp"],
    "deny": ["_verifier/", "matmul_ref.py", "matmul_wrapper.py"]
  },
  "process_verifiers": [
    {
      "name": "correctness",
      "role": "process_gate",
      "command": ["python", "_verifier/verify.py",
                  "--op_name", "matmul", "--verify_dir", "_verifier",
                  "--impl_name", "impl"],
      "timeout_seconds": 600
    },
    {
      "name": "latency",
      "role": "ranking_signal",
      "command": ["python", "_verifier/benchmark.py",
                  "--op_name", "matmul", "--verify_dir", "_verifier",
                  "--impl_name", "impl",
                  "--warmup", "5", "--repeats", "50",
                  "--output", "_verifier/perf_result.json"],
      "timeout_seconds": 900
    }
  ],
  "promotion_verifiers": [
    {"name": "frozen_hash_gate", "role": "anti_cheat_gate",
     "command": ["goal-plus-internal", "check-frozen-hashes"]}
  ],
  "budget": {"max_candidates": 4, "max_parallel": 2},
  "strategy": {
    "name": "agent_guided",
    "orchestration_mode": "parallel_loops",
    "worker_host": "codex",
    "worker_agent_type": "search_candidate_agent"
  }
}

verifier_artifact_paths = [
  "_verifier/verify.py",
  "_verifier/benchmark.py",
  "_verifier/_common_utils.py",
  "_verifier/matmul_torch.py",
  "_verifier/matmul_impl.py"
]

search_freeze_spec(spec=<above>, verifier_artifact_paths=verifier_artifact_paths)
search_create(frozen_spec_id=<id>)  # record run_id

Step 4 — drive the search:
- search_plan_next(run_id, requested_k=4)
- author 4 initial independent lane proposals
- search_start_batch(run_id, plan_id, proposals=[...])
- for each candidate:
    session = search_start_agent_session(run_id, candidate_id, directive)
    spawn_agent(task_name=session.launch.task_name,
                message=session.launch.message,
                fork_turns=session.launch.fork_turns)
    search_run_verifier(run_id, candidate_id, "process")
- search_list_history(run_id, top_n=5)
- search_select(run_id)
- search_report(run_id)

Step 5 — report back at the end:
- run_id
- all 4 candidate scores + iteration counts
- selected_candidate_id
- report.md path
- whether latency improved over the source-baseline
  (read the latency of c001's first iteration vs its last)

Do not promote. The user will review the report first.
```

## Notes for the host agent

- The C++ kernel file is the only entry in `edit_surface.allow`. The wrapper
  that loads it via `torch.utils.cpp_extension.load_inline` is denied —
  changing the binding contract is out of scope for this example.
- If the user's wrapper does not already define `ModelNew`, assemble
  `_verifier/matmul_impl.py` so that it imports the binding and defines
  `ModelNew` to call it. Read the wrapper to confirm.
- If `import torch_npu` fails in the candidate environment, the verifier
  falls back to CPU timing. For NPU-side DSLs the host agent should ensure
  `torch_npu` is available before freeze; otherwise the latency numbers will
  not reflect the intended target hardware.
- Repeating-shape reference inputs: the verifier picks up
  `get_input_groups()` automatically if defined; otherwise it uses
  `get_inputs()` as a single-shape run. Pick the form that matches the
  operator's expected deployment shapes.
