from __future__ import annotations

import random
from copy import deepcopy
from typing import Any


GRID_SIZE = 10_000

_CASE_SPECS = (
    (7, 120, 6),
    (19, 144, 7),
    (31, 168, 7),
    (43, 192, 8),
    (59, 216, 8),
    (71, 240, 9),
    (83, 180, 7),
    (97, 228, 9),
)


def _clamp_cell(value: float) -> int:
    return max(0, min(GRID_SIZE - 1, int(round(value))))


def _target_area(rng: random.Random, cluster_index: int) -> int:
    base = rng.randint(18_000, 170_000)
    if cluster_index % 3 == 0:
        base = int(base * 1.35)
    elif cluster_index % 3 == 1:
        base = int(base * 0.75)
    return max(6_000, min(260_000, base))


def generate_case(seed: int, count: int, clusters: int) -> dict[str, Any]:
    rng = random.Random(seed)
    centers = [
        (
            rng.randint(1_200, GRID_SIZE - 1_200),
            rng.randint(1_200, GRID_SIZE - 1_200),
        )
        for _ in range(clusters)
    ]
    used: set[tuple[int, int]] = set()
    ads: list[dict[str, int]] = []

    while len(ads) < count:
        cluster_index = rng.randrange(clusters)
        cx, cy = centers[cluster_index]
        spread = 520 + 130 * (cluster_index % 3) + 3 * len(ads)
        x = _clamp_cell(rng.gauss(cx, spread))
        y = _clamp_cell(rng.gauss(cy, spread))
        if (x, y) in used:
            continue
        used.add((x, y))
        ads.append(
            {
                "x": x,
                "y": y,
                "target_area": _target_area(rng, cluster_index),
            }
        )

    return {
        "case_id": f"public_seed_{seed}",
        "grid_size": GRID_SIZE,
        "ads": ads,
    }


def public_cases() -> list[dict[str, Any]]:
    return [generate_case(seed, count, clusters) for seed, count, clusters in _CASE_SPECS]


def copy_public_cases() -> list[dict[str, Any]]:
    return deepcopy(public_cases())
