from __future__ import annotations

import importlib.util
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

from cases import GRID_SIZE, copy_public_cases


def _load_program(program_path: str):
    path = Path(program_path).resolve()
    spec = importlib.util.spec_from_file_location("candidate_ad_placement", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load program from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _int_coord(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean coordinate is not valid")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ValueError(f"coordinate must be an integer, got {value!r}")


def _coerce_rectangles(raw: Any, expected: int) -> list[tuple[int, int, int, int]]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError("solution must be a list of rectangles")
    if len(raw) != expected:
        raise ValueError(f"expected {expected} rectangles, got {len(raw)}")

    rectangles: list[tuple[int, int, int, int]] = []
    for index, rect in enumerate(raw):
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            raise ValueError(f"rectangle {index} must be [x1, y1, x2, y2]")
        x1, y1, x2, y2 = (_int_coord(value) for value in rect)
        rectangles.append((x1, y1, x2, y2))
    return rectangles


def _rectangles_overlap(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> bool:
    return (
        max(left[0], right[0]) < min(left[2], right[2])
        and max(left[1], right[1]) < min(left[3], right[3])
    )


def _satisfaction(actual_area: int, target_area: int) -> float:
    if actual_area <= 0 or target_area <= 0:
        return 0.0
    ratio = min(actual_area / target_area, target_area / actual_area)
    return 1.0 - (1.0 - ratio) ** 2


def _evaluate_case(case: dict[str, Any], raw_rectangles: Any) -> dict[str, Any]:
    ads = case["ads"]
    errors: list[str] = []
    try:
        rectangles = _coerce_rectangles(raw_rectangles, len(ads))
    except Exception as exc:
        return {
            "case_id": case["case_id"],
            "valid": False,
            "score": 0.0,
            "average_satisfaction": 0.0,
            "total_area": 0,
            "errors": [str(exc)],
        }

    total_area = 0
    satisfaction_sum = 0.0
    min_satisfaction = 1.0

    for index, (ad, rect) in enumerate(zip(ads, rectangles)):
        x1, y1, x2, y2 = rect
        if not (0 <= x1 < x2 <= GRID_SIZE and 0 <= y1 < y2 <= GRID_SIZE):
            errors.append(f"ad {index}: rectangle is outside grid or empty")
            continue
        if not (x1 <= ad["x"] < x2 and y1 <= ad["y"] < y2):
            errors.append(f"ad {index}: rectangle does not contain anchor point")
            continue
        area = (x2 - x1) * (y2 - y1)
        total_area += area
        score = _satisfaction(area, int(ad["target_area"]))
        satisfaction_sum += score
        min_satisfaction = min(min_satisfaction, score)

    if not errors:
        for left in range(len(rectangles)):
            for right in range(left + 1, len(rectangles)):
                if _rectangles_overlap(rectangles[left], rectangles[right]):
                    errors.append(f"rectangles {left} and {right} overlap")
                    if len(errors) >= 10:
                        break
            if len(errors) >= 10:
                break

    valid = not errors
    average_satisfaction = satisfaction_sum / len(ads) if valid and ads else 0.0
    return {
        "case_id": case["case_id"],
        "valid": valid,
        "score": average_satisfaction * 100.0,
        "average_satisfaction": average_satisfaction,
        "min_satisfaction": min_satisfaction if valid else 0.0,
        "total_area": total_area if valid else 0,
        "errors": errors[:10],
        "ads": len(ads),
    }


def _candidate_outputs(program: Any, cases: list[dict[str, Any]]) -> list[Any]:
    if hasattr(program, "solve_all"):
        raw = program.solve_all(deepcopy(cases))
        if isinstance(raw, dict):
            return [raw.get(case["case_id"]) for case in cases]
        if isinstance(raw, (list, tuple)):
            if len(raw) != len(cases):
                raise ValueError(f"solve_all returned {len(raw)} case outputs for {len(cases)} cases")
            return list(raw)
        raise ValueError("solve_all must return a dict or list")

    if hasattr(program, "solve_case"):
        return [program.solve_case(deepcopy(case)) for case in cases]

    if hasattr(program, "run_ad_placement"):
        raw = program.run_ad_placement(deepcopy(cases))
        if isinstance(raw, dict):
            return [raw.get(case["case_id"]) for case in cases]
        if isinstance(raw, (list, tuple)):
            return list(raw)

    raise ValueError("missing solve_case, solve_all, or run_ad_placement")


def evaluate(program_path: str = "initial_program.py") -> dict[str, Any]:
    try:
        cases = copy_public_cases()
        program = _load_program(program_path)
        outputs = _candidate_outputs(program, cases)
        details = [_evaluate_case(case, output) for case, output in zip(cases, outputs)]
        valid_cases = sum(1 for item in details if item["valid"])
        combined_score = sum(float(item["score"]) for item in details) / len(details)
        validity = valid_cases / len(details)
        total_ads = sum(int(item.get("ads", 0)) for item in details)
        summary = (
            f"{valid_cases}/{len(details)} public cases valid; "
            f"combined_score={combined_score:.6f}; total_ads={total_ads}"
        )
        return {
            "combined_score": combined_score,
            "validity": validity,
            "valid_cases": valid_cases,
            "total_cases": len(details),
            "total_ads": total_ads,
            "summary": summary,
            "details": details,
        }
    except Exception as exc:
        return {
            "combined_score": 0.0,
            "validity": 0.0,
            "valid_cases": 0,
            "total_cases": 0,
            "total_ads": 0,
            "error": str(exc),
        }


if __name__ == "__main__":
    print(json.dumps(evaluate(), sort_keys=True))

