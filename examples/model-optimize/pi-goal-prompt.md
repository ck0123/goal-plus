/goal-plus Optimize the torch CPU model in `examples/model-optimize/torch-cpu-target`.

Improve `tokens_per_second` from `benchmark.py` while keeping `verify.py`
valid. The model must keep using a single CPU core; do not raise PyTorch,
OpenMP, MKL, or build job thread counts.

Use Goal Plus normally: analyze first, discover the metric/verifier/edit
surface, and open Search Mode only if the verifier-backed optimization is
ready. Because this run is under Pi, freeze the SearchSpec with
`strategy.worker_host="pi-rpc"` and
`strategy.worker_mode="agent-session-pool"`. Search workers may edit
`model.py`, `serving.py`, and optional new implementation files under `ops/` or
`cpp_ops/`, but they must not edit `verify.py`, `benchmark.py`,
`workload.json`, `single_thread.py`, or `.pi` assets.

The vector tail in `model.py` may be fused into a C++ CPU operator if that is
worth exploring. Use `cpp_reference/fused_vector_tail.cpp` as the reference
pattern for a single-threaded torch CPU extension, and keep build jobs at
`MAX_JOBS=1`. The final answer should report whether Search was opened, which
candidate was selected, and the final `tokens_per_second`.
