from __future__ import annotations

from single_thread import configure_single_thread

configure_single_thread()

import torch

from single_thread import configure_torch_threads

configure_torch_threads(torch)


FEATURE_DIM = 32
HIDDEN_DIM = 32


class TinyCpuModel:
    def __init__(self) -> None:
        values = torch.arange(FEATURE_DIM * HIDDEN_DIM, dtype=torch.float32)
        self.proj = (values.reshape(FEATURE_DIM, HIDDEN_DIM).remainder(29) - 14.0) / 53.0
        self.bias = torch.linspace(-0.05, 0.05, HIDDEN_DIM, dtype=torch.float32)
        tail = torch.arange(HIDDEN_DIM * HIDDEN_DIM, dtype=torch.float32)
        self.tail = (tail.reshape(HIDDEN_DIM, HIDDEN_DIM).remainder(19) - 9.0) / 47.0

    def encode(self, features: torch.Tensor) -> torch.Tensor:
        return torch.relu(features @ self.proj + self.bias)

    def vector_tail(self, hidden: torch.Tensor) -> torch.Tensor:
        # Intentional fusion opportunity: several elementwise vector ops at the
        # end of the model. A custom C++ CPU operator can compute this in one
        # pass while preserving the single-core constraint.
        a = hidden * 1.03125 + 0.125
        b = torch.relu(a)
        c = b * b
        d = c + b * 0.5
        e = torch.sqrt(torch.clamp(d + 0.001, min=0.0))
        return e * 0.75 + b * 0.25

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        hidden = self.encode(features)
        return self.vector_tail(hidden @ self.tail)


def make_features(batch_size: int, feature_dim: int, step: int) -> torch.Tensor:
    base = torch.arange(batch_size * feature_dim, dtype=torch.float32).reshape(
        batch_size, feature_dim
    )
    return ((base.remainder(17) + step) / 17.0) - 0.5
