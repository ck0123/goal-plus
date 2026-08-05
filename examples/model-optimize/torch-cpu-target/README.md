# Torch CPU Optimization Target

This directory is a minimal model workspace for a real `/goal-plus` run.

The user prompt is intentionally simple: point Goal Plus at this workspace and
ask it to improve `tokens_per_second` without breaking `verify.py`. Goal Plus
should inspect the code, discover the benchmark/verifier/edit surface, and only
then decide whether to open Search Mode.

Hard constraint: every script forces PyTorch to one CPU core. Optimizations that
increase thread count are invalid.

Useful commands:

```bash
python verify.py
python benchmark.py
python profile.py
```

## Multi-model validation

`multi-model-search-spec.json` is a two-slot Goal Plus Search spec for
validating static model binding: one `gpt-5.6-terra` lane and one
`gpt-5.6-sol` lane, sharing the run Annotated Evidence. The reproducible
`scaling`-environment launch command and acceptance evidence are in
[`multi-model-run.md`](multi-model-run.md).

The target includes two intentionally obvious opportunities:

- `fuse_vector_tail`: the last vector path in `model.py` is a sequence of
  elementwise operations that can be fused into a custom C++ CPU operator.
The example prompt and this workspace README explain the domain rules. The
reference C++ CPU operator pattern is in
`cpp_reference/fused_vector_tail.cpp`.

## Shared-dir validation

[`shared-dir-experiment.md`](shared-dir-experiment.md) validates the
verifier-settled shared-dir path with producer and adopter candidates. It
checks staging, publication, discovery, candidate-local revalidation, duplicate
consumption, and final independence from the run shared directory.
