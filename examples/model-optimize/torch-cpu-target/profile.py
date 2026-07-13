from __future__ import annotations

import json

from single_thread import configure_single_thread

configure_single_thread()

import torch

from single_thread import configure_torch_threads

configure_torch_threads(torch)


def profile() -> dict:
    return {
        "valid": True,
        "torch_num_threads": torch.get_num_threads(),
        "opportunities": [
            {
                "id": "fuse_vector_tail",
                "kind": "vector-op-fusion",
                "files": ["model.py"],
                "symbol": "TinyCpuModel.vector_tail",
                "metric": "tokens_per_second",
                "suggested_mode": "search",
                "evidence": "The tail is a chain of elementwise torch ops over the final hidden tensor.",
            },
        ],
    }


if __name__ == "__main__":
    print(json.dumps(profile(), sort_keys=True))
