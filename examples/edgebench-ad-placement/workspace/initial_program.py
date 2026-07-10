from __future__ import annotations

from typing import Any


def solve_case(case: dict[str, Any]) -> list[list[int]]:
    """Return a conservative legal baseline: one grid cell per ad."""
    rectangles: list[list[int]] = []
    grid_size = int(case.get("grid_size", 10_000))
    for ad in case["ads"]:
        x = int(ad["x"])
        y = int(ad["y"])
        rectangles.append([x, y, min(x + 1, grid_size), min(y + 1, grid_size)])
    return rectangles


def solve_all(cases: list[dict[str, Any]]) -> dict[str, list[list[int]]]:
    return {case["case_id"]: solve_case(case) for case in cases}


if __name__ == "__main__":
    import json
    from cases import copy_public_cases

    print(json.dumps(solve_all(copy_public_cases()), sort_keys=True))
