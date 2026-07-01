from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from typing import Any


TARGET_SUM_RADII = 2.635
N_CIRCLES = 26


def _load_program(program_path: str):
    path = Path(program_path).resolve()
    spec = importlib.util.spec_from_file_location("candidate_program", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load program from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _as_float_pair(value: Any) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"center must be a pair, got {value!r}")
    return float(value[0]), float(value[1])


def _coerce_solution(result: Any) -> tuple[list[tuple[float, float]], list[float], float | None]:
    if not isinstance(result, (list, tuple)) or len(result) not in {2, 3}:
        raise ValueError("run_packing must return (centers, radii) or (centers, radii, sum_radii)")

    centers = [_as_float_pair(center) for center in result[0]]
    radii = [float(radius) for radius in result[1]]
    reported_sum = float(result[2]) if len(result) == 3 else None
    return centers, radii, reported_sum


def _validate(centers: list[tuple[float, float]], radii: list[float]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if len(centers) != N_CIRCLES or len(radii) != N_CIRCLES:
        errors.append(f"expected {N_CIRCLES} centers/radii, got {len(centers)}/{len(radii)}")
        return False, errors

    for index, ((x, y), radius) in enumerate(zip(centers, radii)):
        if not all(math.isfinite(value) for value in (x, y, radius)):
            errors.append(f"circle {index} has non-finite values")
            continue
        if radius < -1e-9:
            errors.append(f"circle {index} has negative radius")
        if x - radius < -1e-6 or x + radius > 1.0 + 1e-6:
            errors.append(f"circle {index} violates x boundary")
        if y - radius < -1e-6 or y + radius > 1.0 + 1e-6:
            errors.append(f"circle {index} violates y boundary")

    for left in range(len(centers)):
        for right in range(left + 1, len(centers)):
            lx, ly = centers[left]
            rx, ry = centers[right]
            distance = math.hypot(lx - rx, ly - ry)
            if distance + 1e-6 < radii[left] + radii[right]:
                errors.append(f"circles {left} and {right} overlap")
                if len(errors) >= 10:
                    return False, errors

    return not errors, errors


def evaluate(program_path: str) -> dict[str, Any]:
    try:
        program = _load_program(program_path)
        if hasattr(program, "run_packing"):
            result = program.run_packing()
        elif hasattr(program, "construct_packing"):
            result = program.construct_packing()
        else:
            return {
                "combined_score": 0.0,
                "validity": 0.0,
                "error": "missing run_packing or construct_packing",
            }

        centers, radii, reported_sum = _coerce_solution(result)
        valid, errors = _validate(centers, radii)
        actual_sum = sum(radii) if valid else 0.0
        target_ratio = actual_sum / TARGET_SUM_RADII if valid else 0.0
        reported_delta = abs(reported_sum - sum(radii)) if reported_sum is not None else 0.0

        return {
            "combined_score": float(target_ratio),
            "sum_radii": float(actual_sum),
            "target_ratio": float(target_ratio),
            "validity": 1.0 if valid else 0.0,
            "min_radius": float(min(radii)) if radii else 0.0,
            "max_radius": float(max(radii)) if radii else 0.0,
            "avg_radius": float(sum(radii) / len(radii)) if radii else 0.0,
            "reported_sum_delta": float(reported_delta),
            "errors": errors[:5],
        }
    except Exception as exc:
        return {
            "combined_score": 0.0,
            "sum_radii": 0.0,
            "target_ratio": 0.0,
            "validity": 0.0,
            "error": str(exc),
        }


if __name__ == "__main__":
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "initial_program.py"
    print(json.dumps(evaluate(target), sort_keys=True))
