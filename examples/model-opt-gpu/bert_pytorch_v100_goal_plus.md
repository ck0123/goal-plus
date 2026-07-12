# WIP Trigger Prompt: BERT_pytorch V100 Model Optimization

> WIP and not validated. Paste this into a host that has `/goal-plus`, a V100,
> working CUDA PyTorch, Git, and network access. Do not use it on the current
> non-CUDA development machine.

```text
/goal-plus

Prepare and then continuously optimize the current TorchBench BERT_pytorch CUDA
eval workload on the V100 environment available to you. The success target is
at least 10% lower median latency while preserving the frozen eager-CUDA
outputs. Treat the current machine and its installed PyTorch/CUDA/Triton stack
as authoritative. Record observed versions and commits for provenance, but do
not require a predetermined TorchBench, PyTorch, or CUDA commit.

Do not start Search Mode and do not freeze a SearchSpec until every preparation
gate below is complete. Do not invent a baseline, speedup, correctness result,
GPU count, or CUDA capability.

Preparation gate 1 -- environment and resources

1. Verify nvidia-smi, torch.cuda.is_available(), the selected GPU names,
   compute capabilities, free memory, and a basic PyTorch CUDA operation.
2. Record Python, torch, CUDA runtime, Triton, driver, and relevant package
   versions. A V100 is expected, but report the devices actually present.
3. Build an explicit resource manifest. Define detected_gpu_count as the number
   of usable physical GPUs explicitly assigned to this run. Map at most one live
   worker to each physical device and pass CUDA_VISIBLE_DEVICES in that worker's
   launch environment or directive.
4. Derive SearchSpec.budget.max_parallel from detected_gpu_count. Never hard-code
   one global concurrency value. If only one V100 is usable, run GPU workers
   sequentially while retaining multiple candidate workspaces and batches.

Preparation gate 2 -- obtain current TorchBench source

1. Reuse a suitable existing pytorch/benchmark checkout or clone
   https://github.com/pytorch/benchmark.git into ignored preparation storage.
2. Record git rev-parse HEAD and git status in provenance.json. Do not mutate the
   user's checkout. Do not require a commit chosen in advance.
3. Locate torchbenchmark/models/BERT_pytorch and inspect its current wrapper,
   model package, dependencies, license, eval batch size, seeds, corpus/input
   generation, sequence shape, dtype, and no-grad behavior.

Preparation gate 3 -- create a standalone complete network

1. Create prepared/BERT_pytorch_standalone outside the TorchBench checkout.
2. Copy the complete BERT_pytorch/bert_pytorch package, license, and relevant
   dependency metadata. Add an initially empty triton_ops/ package.
3. Write local runner.py, benchmark.py, and verifier.py. Reproduce the current
   TorchBench eval workload exactly, but remove runtime imports of torchbenchmark
   and its public BenchmarkModel. The TorchBench wrapper is reference material,
   not a dependency of the prepared runtime.
4. Audit the prepared runtime and fail if it imports torchbenchmark or
   BenchmarkModel. Verify that it can construct the model and run CUDA eval from
   only the prepared directory and installed third-party packages.
5. Initialize this prepared directory as the complete candidate source tree.
   SearchSpec.source_path must point to it, not to a wrapper around an external
   model and not to the full TorchBench repository.

Preparation gate 4 -- correctness and timing

1. Fix seeds and input tensors. Run eager CUDA eval and store the exact input
   metadata and reference output tensors outside the candidate edit surface.
2. Define a public correctness gate that rejects exceptions, timeouts, CUDA
   failures, non-finite outputs, shape/dtype changes, and output differences
   beyond a tolerance measured and justified on this environment.
3. Define synchronized CUDA timing with warmup iterations and repeated raw
   samples. Use torch.cuda.synchronize or correctly paired CUDA events around
   the measured region and report median_latency_ms.
4. Treat compilation and autotune startup consistently for baseline and
   candidates. Keep benchmark inputs, warmup, sample count, synchronization,
   and result parsing frozen.
5. Run the real baseline. Save raw samples and baseline_median_latency_ms. Set
   the success threshold to 0.90 times that measured value. Do not invent a
   baseline or continue if measurement is unstable or correctness is not
   reproducible.

Optional AI-infra evidence

Use BBuf/AI-Infra-Auto-Driven-SKILLS as supporting analysis when available.
Prefer llm-torch-profiler-analysis for compatible single-trace kernel/fusion
triage and llm-pipeline-analysis for compatible layer/kernel drill-down. These
skills are serving-oriented: if their assumptions or parsers do not fit the
standalone BERT trace, record the incompatibility and use direct torch.profiler
evidence instead. Do not alter the workload merely to satisfy a skill.

SearchSpec autonomous readiness contract

After all preparation gates pass, autonomously draft and freeze a SearchSpec
with these properties; do not wait for user confirmation:

- objective: reduce measured BERT_pytorch V100 CUDA eval median latency by at
  least 10% relative to baseline_median_latency_ms;
- metric_name: median_latency_ms;
- metric_direction: minimize;
- source_path: the complete prepared/BERT_pytorch_standalone directory;
- workspace.backend: git_worktree;
- max_candidates: choose a bounded campaign size appropriate for available
  time; candidate count may exceed GPU count;
- max_parallel: derived from detected_gpu_count and the explicit resource map;
- allow: bert_pytorch/**, runner integration needed for optimized kernels, and
  triton_ops/**;
- deny: verifier.py, benchmark policy, frozen inputs/references,
  provenance.json, and result parsing;
- process verifier: correctness first, synchronized latency second;
- promotion rule: only a clean verifier-backed Git iteration may be selected.

Candidate work and continuous optimization

Give each candidate a complete editable git_worktree workspace and its assigned
GPU. Let workers inspect code and profiler evidence before choosing a change.
Useful hypotheses include attention and mask/softmax changes, LayerNorm,
feed-forward blocks, tensor layout, allocation removal, safe operator fusion,
and new V100-compatible kernels under triton_ops/. Do not prescribe Triton when
the measured bottleneck points elsewhere.

The main agent owns resource assignment, batch planning, verifier readiness,
selection, and follow-up batches. The runtime does not schedule GPUs or own
worker lifecycle. Continue bounded search until one of these occurs:

- a correctness-valid candidate reaches the 10% target;
- the candidate/time budget is exhausted;
- repeated evidence shows the target is not achievable under the frozen
  workload and allowed edit surface.

Final evidence must include the observed environment and TorchBench commit,
standalone source path, GPU resource map, baseline raw samples and median,
candidate scores and branches, correctness tolerance/results, selected clean
Git commit, achieved speedup, report path, and any remaining WIP limitations.
Never claim that this repository's documentation-only WIP was GPU-validated.
```
