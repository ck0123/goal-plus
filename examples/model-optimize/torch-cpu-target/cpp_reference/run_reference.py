from __future__ import annotations

import json
import os
from pathlib import Path

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["MAX_JOBS"] = "1"

import torch
from torch.utils.cpp_extension import load


ROOT = Path(__file__).resolve().parent


def main() -> dict:
    torch.set_num_threads(1)
    load(
        name="cpu_torch_vector_opt_reference",
        sources=[str(ROOT / "fused_vector_tail.cpp")],
        extra_cflags=["-O3"],
        is_python_module=False,
        verbose=False,
    )
    sample = torch.linspace(-1.0, 1.0, 128, dtype=torch.float32)
    expected = torch.sqrt(torch.clamp(torch.relu(sample * 1.03125 + 0.125) ** 2 + torch.relu(sample * 1.03125 + 0.125) * 0.5 + 0.001, min=0.0)) * 0.75 + torch.relu(sample * 1.03125 + 0.125) * 0.25
    actual = torch.ops.cpu_torch_vector_opt.fused_vector_tail(sample)
    max_abs_error = float((actual - expected).abs().max().item())
    return {
        "valid": max_abs_error < 1e-6,
        "max_abs_error": max_abs_error,
        "torch_num_threads": torch.get_num_threads(),
        "extension_loaded": True,
    }


if __name__ == "__main__":
    print(json.dumps(main(), sort_keys=True))
