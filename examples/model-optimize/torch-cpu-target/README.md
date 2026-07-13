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

The target includes two intentionally obvious opportunities:

- `fuse_vector_tail`: the last vector path in `model.py` is a sequence of
  elementwise operations that can be fused into a custom C++ CPU operator.
The example prompt and this workspace README explain the domain rules. The
reference C++ CPU operator pattern is in
`cpp_reference/fused_vector_tail.cpp`.
