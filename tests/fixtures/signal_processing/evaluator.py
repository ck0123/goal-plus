from __future__ import annotations

import importlib.util
import math
import random
import time
from pathlib import Path
from typing import Any


WINDOW_SIZE = 20


def _load_program(program_path: str):
    path = Path(program_path).resolve()
    spec = importlib.util.spec_from_file_location("candidate_program", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load program from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: list[float]) -> float:
    if not values:
        return 0.0
    center = _mean(values)
    return sum((value - center) ** 2 for value in values) / len(values)


def _correlation(left: list[float], right: list[float]) -> float:
    count = min(len(left), len(right))
    if count < 2:
        return 0.0
    xs = left[:count]
    ys = right[:count]
    mx = _mean(xs)
    my = _mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    left_var = sum((x - mx) ** 2 for x in xs)
    right_var = sum((y - my) ** 2 for y in ys)
    denominator = math.sqrt(left_var * right_var)
    return numerator / denominator if denominator > 0.0 else 0.0


def _slope_changes(values: list[float]) -> int:
    if len(values) < 3:
        return 0
    changes = 0
    previous = 0
    for left, right in zip(values, values[1:]):
        delta = right - left
        sign = 1 if delta > 0 else -1 if delta < 0 else 0
        if sign and previous and sign != previous:
            changes += 1
        if sign:
            previous = sign
    return changes


def _false_reversals(filtered: list[float], clean: list[float]) -> int:
    count = min(len(filtered), len(clean))
    if count < 3:
        return 0
    filtered_changes = []
    clean_changes = []
    for values, output in ((filtered[:count], filtered_changes), (clean[:count], clean_changes)):
        previous = 0
        for left, right in zip(values, values[1:]):
            delta = right - left
            sign = 1 if delta > 0 else -1 if delta < 0 else 0
            output.append(bool(sign and previous and sign != previous))
            if sign:
                previous = sign
    return sum(1 for lhs, rhs in zip(filtered_changes, clean_changes) if lhs and not rhs)


def _generate_test_signals() -> list[tuple[list[float], list[float]]]:
    signals = []
    for case in range(5):
        rng = random.Random(42 + case)
        length = 240 + case * 40
        noise_level = 0.18 + case * 0.05
        clean = []

        for index in range(length):
            t = index / max(1, length - 1) * 10.0
            if case == 0:
                value = 2.0 * math.sin(2.0 * math.pi * 0.5 * t) + 0.1 * t
            elif case == 1:
                value = (
                    math.sin(2.0 * math.pi * 0.5 * t)
                    + 0.5 * math.sin(2.0 * math.pi * 2.0 * t)
                    + 0.2 * math.sin(2.0 * math.pi * 5.0 * t)
                )
            elif case == 2:
                value = math.sin(2.0 * math.pi * (0.5 + 0.2 * t) * t)
            elif case == 3:
                value = 1.0 if index < length // 3 else 2.0 if index < 2 * length // 3 else 0.5
            else:
                last = clean[-1] if clean else 0.0
                value = last + rng.gauss(0.0, 0.08) + 0.005
            clean.append(value)

        noisy = [value + rng.gauss(0.0, noise_level) for value in clean]
        signals.append((noisy, clean))

    return signals


def _coerce_filtered(result: Any) -> list[float]:
    if isinstance(result, dict):
        result = result.get("filtered_signal", [])
    return [float(value) for value in result]


def evaluate(program_path: str) -> dict[str, Any]:
    try:
        program = _load_program(program_path)
        if not hasattr(program, "process_signal") and not hasattr(program, "run_signal_processing"):
            return {"overall_score": 0.0, "combined_score": 0.0, "error": "missing signal processing function"}

        per_case = []
        start = time.perf_counter()
        for noisy, clean in _generate_test_signals():
            if hasattr(program, "process_signal"):
                filtered = _coerce_filtered(program.process_signal(noisy, WINDOW_SIZE))
            else:
                filtered = _coerce_filtered(
                    program.run_signal_processing(input_signal=noisy, window_size=WINDOW_SIZE)
                )

            expected_length = len(noisy) - WINDOW_SIZE + 1
            if len(filtered) != expected_length:
                per_case.append({"overall_score": 0.0, "error": "wrong output length"})
                continue
            if not all(math.isfinite(value) for value in filtered):
                per_case.append({"overall_score": 0.0, "error": "non-finite output"})
                continue

            aligned_clean = clean[WINDOW_SIZE - 1 :]
            aligned_noisy = noisy[WINDOW_SIZE - 1 :]
            errors_clean = [abs(lhs - rhs) for lhs, rhs in zip(filtered, aligned_clean)]
            errors_noisy = [abs(lhs - rhs) for lhs, rhs in zip(filtered, aligned_noisy)]
            noisy_errors = [lhs - rhs for lhs, rhs in zip(aligned_noisy, aligned_clean)]
            filtered_errors = [lhs - rhs for lhs, rhs in zip(filtered, aligned_clean)]

            slope_changes = _slope_changes(filtered)
            false_reversals = _false_reversals(filtered, aligned_clean)
            lag_error = errors_noisy[-1] if errors_noisy else 1.0
            avg_error = _mean(errors_noisy)
            correlation = max(0.0, _correlation(filtered, aligned_clean))
            before_var = _variance(noisy_errors)
            after_var = _variance(filtered_errors)
            noise_reduction = max(0.0, (before_var - after_var) / before_var) if before_var else 0.0

            smoothness_score = 1.0 / (1.0 + slope_changes / 20.0)
            responsiveness_score = 1.0 / (1.0 + lag_error + avg_error)
            clean_error_score = 1.0 / (1.0 + _mean(errors_clean))
            reversal_score = 1.0 / (1.0 + false_reversals / 10.0)
            overall_score = (
                0.25 * smoothness_score
                + 0.20 * responsiveness_score
                + 0.25 * correlation
                + 0.15 * noise_reduction
                + 0.10 * clean_error_score
                + 0.05 * reversal_score
            )

            per_case.append(
                {
                    "overall_score": overall_score,
                    "smoothness_score": smoothness_score,
                    "responsiveness_score": responsiveness_score,
                    "correlation": correlation,
                    "noise_reduction": noise_reduction,
                    "clean_error_score": clean_error_score,
                    "false_reversals": false_reversals,
                    "slope_changes": slope_changes,
                }
            )

        elapsed = time.perf_counter() - start
        successful = [case for case in per_case if "error" not in case]
        if not successful:
            return {
                "overall_score": 0.0,
                "combined_score": 0.0,
                "success_rate": 0.0,
                "error": "all signal cases failed",
            }

        aggregate = {
            key: _mean([float(case[key]) for case in successful])
            for key in (
                "overall_score",
                "smoothness_score",
                "responsiveness_score",
                "correlation",
                "noise_reduction",
                "clean_error_score",
            )
        }
        aggregate["combined_score"] = aggregate["overall_score"]
        aggregate["success_rate"] = len(successful) / len(per_case)
        aggregate["execution_time"] = elapsed
        return aggregate
    except Exception as exc:
        return {
            "overall_score": 0.0,
            "combined_score": 0.0,
            "success_rate": 0.0,
            "error": str(exc),
        }


if __name__ == "__main__":
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "initial_program.py"
    print(json.dumps(evaluate(target), sort_keys=True))
