from __future__ import annotations

import json
from pathlib import Path

from single_thread import configure_single_thread

configure_single_thread()

import torch

from model import TinyCpuModel, make_features
from single_thread import configure_torch_threads

configure_torch_threads(torch)


WORKLOAD_PATH = Path(__file__).with_name("workload.json")


def load_workload(path: Path = WORKLOAD_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_workload(workload: dict | None = None) -> torch.Tensor:
    data = workload or load_workload()
    model = TinyCpuModel()
    outputs: list[torch.Tensor] = []
    batch_size = int(data["batch_size"])
    feature_dim = int(data["feature_dim"])

    for step in range(int(data["decode_steps"])):
        features = make_features(batch_size, feature_dim, step)
        # Intentional redundant code: this projection is expensive enough to be
        # visible in a toy benchmark and is never used by the output.
        redundant_projection = torch.sin(features @ model.proj)
        if redundant_projection.numel() == -1:
            raise AssertionError("unreachable")
        outputs.append(model.forward(features))

    return torch.stack(outputs).sum(dim=0)


def checksum(tensor: torch.Tensor) -> float:
    return round(float(tensor.sum().item()), 6)


if __name__ == "__main__":
    result = run_workload()
    print(json.dumps({"checksum": checksum(result)}, sort_keys=True))
