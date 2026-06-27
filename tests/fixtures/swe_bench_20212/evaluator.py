"""Evaluator for the SWE-bench sympy__sympy-20212 fixture.

Mirrors SWE-bench's evaluation contract: each candidate is scored by how many
of the original test assertions pass. We split them into:

- FAIL_TO_PASS: assertions from the upstream `test_patch` (issue 19572). These
  fail on the buggy baseline and pass after the gold patch.
- PASS_TO_PASS: assertions the baseline already passes. These guard against
  trivial fixes that break other behavior.

The `combined_score` rewards passing FAIL_TO_PASS and penalizes PASS_TO_PASS
regressions, so candidates cannot score full marks by simply returning
`COMPLEX_INFINITY` unconditionally.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load_program(program_path: str):
    path = Path(program_path).resolve()
    spec = importlib.util.spec_from_file_location("candidate_program", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load program from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Each row: (label, base_name, exponent_name, expected_name, is_fail_to_pass).
# Symbol names must match the singletons exported by `initial_program.py`.
# FAIL_TO_PASS rows are translated from `sympy/core/tests/test_power.py::test_zero`
# (the upstream `test_patch`):
#     assert 0 ** -oo is zoo
#     assert power(0, -oo) is zoo
TEST_CASES: list[tuple[str, str, str, str, bool]] = [
    ("0**-oo is zoo",        "ZERO",  "NEG_INFINITY",  "COMPLEX_INFINITY", True),
    ("power(0, -oo) is zoo", "ZERO",  "NEG_INFINITY",  "COMPLEX_INFINITY", True),
    # PASS_TO_PASS: behavior the baseline already gets right.
    ("0**0 is 1",            "ZERO",  "ZERO",          "ONE",              False),
    ("0**oo is 0",           "ZERO",  "INFINITY",      "ZERO",             False),
    ("0**zoo is nan",        "ZERO",  "COMPLEX_INFINITY", "NAN",           False),
    ("1**-oo is 1",          "ONE",   "NEG_INFINITY",  "ONE",              False),
]


def _resolve(module, name: str):
    value = getattr(module, name, None)
    if value is None:
        raise AttributeError(f"program does not export symbol '{name}'")
    return value


def evaluate(program_path: str) -> dict[str, Any]:
    try:
        program = _load_program(program_path)
        if not hasattr(program, "evaluate_power"):
            return {
                "combined_score": 0.0,
                "valid": False,
                "error": "missing evaluate_power",
            }

        results = []
        ftp_pass = ftp_total = 0
        ptp_pass = ptp_total = 0

        for label, base_name, exp_name, expected_name, is_ftp in TEST_CASES:
            base = _resolve(program, base_name)
            exponent = _resolve(program, exp_name)
            expected = _resolve(program, expected_name)
            try:
                actual = program.evaluate_power(base, exponent)
                passed = actual == expected
                detail = repr(actual)
            except Exception as exc:  # noqa: BLE001 - surface failure in report
                passed = False
                detail = f"<exception: {exc!r}>"

            results.append(
                {
                    "label": label,
                    "passed": bool(passed),
                    "is_fail_to_pass": is_ftp,
                    "actual": detail,
                    "expected": repr(expected),
                }
            )

            if is_ftp:
                ftp_total += 1
                ftp_pass += int(passed)
            else:
                ptp_total += 1
                ptp_pass += int(passed)

        if ftp_total == 0:
            return {"combined_score": 0.0, "valid": False, "error": "no FAIL_TO_PASS cases configured"}

        ftp_rate = ftp_pass / ftp_total
        ptp_rate = (ptp_pass / ptp_total) if ptp_total else 1.0

        # Combined score: FAIL_TO_PASS is the primary signal; PASS_TO_PASS acts
        # as a no-regression multiplier. A candidate that hard-codes
        # COMPLEX_INFINITY still passes both FAIL_TO_PASS rows but breaks the
        # `0**0 is 1` PASS_TO_PASS row, so it cannot reach 1.0.
        combined_score = ftp_rate * (0.7 + 0.3 * ptp_rate)

        return {
            "combined_score": float(combined_score),
            "valid": True,
            "fail_to_pass_passed": ftp_pass,
            "fail_to_pass_total": ftp_total,
            "pass_to_pass_passed": ptp_pass,
            "pass_to_pass_total": ptp_total,
            "results": results,
        }
    except Exception as exc:  # noqa: BLE001 - report load/eval errors as score 0
        return {
            "combined_score": 0.0,
            "valid": False,
            "error": str(exc),
        }


if __name__ == "__main__":
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "initial_program.py"
    print(json.dumps(evaluate(target), sort_keys=True))
