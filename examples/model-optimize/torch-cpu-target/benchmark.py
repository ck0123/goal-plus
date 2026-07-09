from __future__ import annotations

import json
import time

from single_thread import configure_single_thread

configure_single_thread()

import torch

from serving import load_workload, run_workload
from single_thread import configure_torch_threads
from verify import verify

configure_torch_threads(torch)


def benchmark(repetitions: int = 24) -> dict:
    workload = load_workload()
    run_workload(workload)

    start = time.perf_counter()
    for _ in range(repetitions):
        run_workload(workload)
    elapsed = max(time.perf_counter() - start, 1e-9)
    latency_ms = elapsed * 1000.0 / repetitions
    tokens = int(workload["batch_size"]) * int(workload["decode_steps"])
    validity = verify()
    score = tokens * repetitions / elapsed if validity["valid"] else 0.0
    return {
        "valid": bool(validity["valid"]),
        "tokens_per_second": round(score, 3),
        "latency_ms": round(latency_ms, 3),
        "torch_num_threads": torch.get_num_threads(),
        "repetitions": repetitions,
        "metric_name": "tokens_per_second",
        "metric_direction": "maximize",
    }


if __name__ == "__main__":
    print(json.dumps(benchmark(), sort_keys=True))
