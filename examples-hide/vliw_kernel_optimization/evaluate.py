#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
TARGETS = {
    "public": (
        ROOT / "worker",
        "edgebench.work.vliw_kernel_optimization:9fa380a0ebef",
    ),
    "hidden": (
        ROOT / "judge",
        "edgebench.judge.vliw_kernel_optimization:5cdef0021634",
    ),
}


def run_local(label: str, solution: Path) -> int:
    directory, _ = TARGETS[label]
    print(f"\n===== {label} / local =====", flush=True)
    completed = subprocess.run(
        [sys.executable, "runner.py", "--solution", str(solution)],
        cwd=directory,
        check=False,
    )
    return completed.returncode


def run_docker(label: str, solution: Path) -> int:
    _, image = TARGETS[label]
    print(f"\n===== {label} / docker ({image}) =====", flush=True)
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{solution}:/submission/solution.py:ro",
            image,
            "python3",
            "runner.py",
            "--solution",
            "/submission/solution.py",
        ],
        check=False,
    )
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the extracted VLIW public and hidden evaluators."
    )
    parser.add_argument(
        "solution",
        nargs="?",
        default=str(ROOT / "worker" / "solution.py"),
        help="solution.py to evaluate (default: worker/solution.py)",
    )
    parser.add_argument(
        "--cases",
        choices=("public", "hidden", "both"),
        default="both",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="also evaluate through the original EdgeBench images",
    )
    args = parser.parse_args()

    solution = Path(args.solution).expanduser().resolve()
    if not solution.is_file():
        parser.error(f"solution does not exist: {solution}")

    labels = ("public", "hidden") if args.cases == "both" else (args.cases,)
    return_codes: list[int] = []
    for label in labels:
        return_codes.append(run_local(label, solution))
        if args.docker:
            return_codes.append(run_docker(label, solution))
    return 0 if all(code == 0 for code in return_codes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
