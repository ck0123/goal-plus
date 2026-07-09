from __future__ import annotations

import json
from pathlib import Path

from single_thread import configure_single_thread

configure_single_thread()

import torch

from serving import checksum, load_workload, run_workload
from single_thread import configure_torch_threads

configure_torch_threads(torch)


ROOT = Path(__file__).resolve().parent


def verify() -> dict:
    workload = load_workload()
    result = run_workload(workload)
    observed = checksum(result)
    expected = float(workload["expected_checksum"])
    valid = abs(observed - expected) <= 1e-4 and torch.get_num_threads() == 1
    return {
        "valid": valid,
        "checksum": observed,
        "expected_checksum": expected,
        "torch_num_threads": torch.get_num_threads(),
        "shape": list(result.shape),
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
