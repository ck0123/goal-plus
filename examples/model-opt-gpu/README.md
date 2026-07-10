# WIP: model-opt-gpu BERT_pytorch on V100

> WIP: this example is incomplete, not validated on CUDA, and not recommended
> for use yet. It is intentionally parked until a V100 environment is
> available. It contains no measured baseline or performance claim.

This directory records a future `/goal-plus` scenario for optimizing the CUDA
eval path of TorchBench `BERT_pytorch`. The intended result is a standalone
copy of the complete network that every Search candidate can edit and run
without importing TorchBench's public `BenchmarkModel` runtime.

The only executable input in this WIP is the paste-ready goal prompt:

- [`bert_pytorch_v100_goal_plus.md`](bert_pytorch_v100_goal_plus.md)

Do not treat this as a working GPU example until the preparation and validation
gates below have been completed on the target machine.

## Why Prepare A Standalone Network

TorchBench keeps the actual BERT implementation under
`torchbenchmark/models/BERT_pytorch/bert_pytorch/`, while the model-level
`__init__.py` adapts it to the shared TorchBench `BenchmarkModel` interface.
Giving Search only a wrapper around an external TorchBench checkout would stop
candidates from safely changing the real attention, LayerNorm, feed-forward,
or tensor-layout code.

The future preparation step must instead create a self-contained source tree:

```text
prepared/BERT_pytorch_standalone/
├── bert_pytorch/             # complete copied model package
├── triton_ops/               # candidate-owned custom kernels
├── runner.py                 # local model/input construction, no BenchmarkModel
├── benchmark.py              # synchronized CUDA timing
├── verifier.py               # correctness + latency process verifier
├── reference_outputs.pt      # frozen eager CUDA reference
├── provenance.json           # observed environment and upstream commit
├── LICENSE
└── README.md
```

`SearchSpec.source_path` points at this whole directory. With
`workspace.backend="git_worktree"`, each candidate gets its own branch and
complete editable checkout while sharing Git objects.

## Future V100 Preparation Flow

### 1. Inspect The Current Environment

The V100 host is authoritative. Record, but do not require predetermined,
versions for:

- GPU name/count and compute capability;
- NVIDIA driver and CUDA runtime;
- Python, PyTorch, and Triton;
- the observed `pytorch/benchmark` commit;
- relevant environment variables and installed dependencies.

Fail before preparing Search if CUDA is unavailable, the selected devices are
not accessible, or a basic PyTorch CUDA operation fails.

### 2. Download Or Reuse TorchBench

Clone `https://github.com/pytorch/benchmark.git` into ignored preparation
storage, or reuse an existing clean checkout. Use the checkout as it exists in
the current environment and record `git rev-parse HEAD` in `provenance.json`.
This WIP deliberately does not impose a TorchBench, PyTorch, or CUDA commit.

### 3. Extract And Decouple BERT_pytorch

Copy the complete `BERT_pytorch/bert_pytorch/` package plus its license and
dependency metadata. Read the current TorchBench wrapper to reproduce its eval
model construction, seeds, corpus generation, batch size, sequence shape, and
inputs in local `runner.py`.

The prepared directory must pass an import audit proving that its runtime does
not import:

```text
torchbenchmark
BenchmarkModel
```

The wrapper remains provenance material only. The standalone harness must
preserve the current workload rather than silently switching to a different
BERT configuration.

### 4. Freeze Correctness Evidence

Run eager CUDA eval with fixed seeds and frozen input tensors. Store reference
outputs outside the candidate edit surface. The public verifier must reject:

- exceptions, CUDA errors, timeouts, or non-finite outputs;
- output shape or dtype changes;
- output differences beyond an explicitly measured tolerance;
- edits to the benchmark, references, verifier, or provenance;
- missing synchronization or changes to warmup/sample policy.

This is an optimization-with-feedback task, not a hidden-answer benchmark, so
workers may see public correctness and latency results.

### 5. Measure The Baseline

Timing must use warmup iterations, repeated samples, and CUDA synchronization
around the measured region (`torch.cuda.synchronize` or correctly paired CUDA
events). Report median latency and retain raw samples. Compilation or autotune
startup must be handled consistently between baseline and candidates.

The target is defined only after measurement:

```text
candidate_median_latency_ms <= 0.90 * baseline_median_latency_ms
```

Do not invent a baseline on a machine that has not run the workload.

### 6. Allocate GPU Resources

The runtime does not schedule GPUs. Before launching a batch, the main host
agent builds an explicit resource map with one physical GPU per live worker and
sets `CUDA_VISIBLE_DEVICES` in that worker's launch environment or directive.

```text
detected_gpu_count = number of usable, explicitly assigned physical GPUs
SearchSpec.budget.max_parallel = detected_gpu_count
```

On one V100 this naturally means one live GPU worker and sequential candidate
batches. On a multi-GPU host, concurrency may increase only when every live
worker has a distinct device. Candidate count is independent of GPU count.

### 7. Freeze SearchSpec And Start Search

Only after the standalone harness, correctness reference, measured baseline,
and resource map exist should the main agent freeze a SearchSpec. Its contract
is:

- metric: `median_latency_ms`, direction `minimize`;
- objective: at least 10% lower median CUDA eval latency;
- source: complete `BERT_pytorch_standalone` directory;
- workspace backend: `git_worktree`;
- allowed edits: model implementation, integration code, and `triton_ops/`;
- denied edits: verifier, benchmark policy, inputs, references, and provenance;
- process verifier: correctness first, synchronized timing second;
- worker concurrency: derived from the resource map, never assumed globally.

Candidate directions may include attention and mask/softmax changes,
LayerNorm, feed-forward blocks, layout/allocation improvements, safe fusion,
and V100-compatible Triton kernels. A speedup that fails correctness is not a
candidate.

## AI-Infra-Auto-Driven-SKILLS Integration

`BBuf/AI-Infra-Auto-Driven-SKILLS` is optional supporting evidence, not the
Search controller. On the GPU host, link or install its
`llm-torch-profiler-analysis` and `llm-pipeline-analysis` skills if their trace
parsers accept the standalone BERT profile. Use their evidence discipline to
identify hot kernels and fusion opportunities.

Those skills are primarily serving-oriented. If a serving-specific assumption
does not apply to this standalone TorchBench workload, record the mismatch and
fall back to direct `torch.profiler` analysis. Do not change the workload to
make a skill appear compatible.

## What Is Still Missing

Before this WIP can be promoted to a supported example, it needs one real V100
run proving:

- standalone extraction and import independence;
- deterministic correctness references and tolerances;
- stable baseline timing and raw samples;
- at least one complete `/goal-plus` candidate cycle;
- correct per-worker GPU assignment;
- final report and promotion behavior.

Until that evidence exists, keep this example marked WIP and not recommended.

## Upstream References

- TorchBench: <https://github.com/pytorch/benchmark>
- BERT_pytorch source:
  <https://github.com/pytorch/benchmark/tree/main/torchbenchmark/models/BERT_pytorch>
- AI infra skills: <https://github.com/BBuf/AI-Infra-Auto-Driven-SKILLS>
