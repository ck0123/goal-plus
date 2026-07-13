#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


PUBLIC_SEEDS = tuple(range(10))
SCORE_PATTERN = re.compile(r"(?:^|\n)Score = (\d+)(?:\n|$)")


def run_checked(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {command!r}\n{output[-2000:]}"
        )
    return completed


def evaluate(workspace: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="goal-plus-ad-") as temporary:
        temp = Path(temporary)
        seeds_file = temp / "seeds.txt"
        cases_dir = temp / "cases"
        solution_binary = temp / "solution"
        seeds_file.write_text("".join(f"{seed}\n" for seed in PUBLIC_SEEDS), encoding="utf-8")

        run_checked(
            [str(workspace / "tools" / "bin" / "gen"), str(seeds_file), "-d", str(cases_dir)],
            cwd=workspace,
            timeout=20,
        )
        compiler = shlex.split(os.environ.get("CXX", "g++"))
        run_checked(
            [*compiler, "-std=c++17", "-O2", "solution.cpp", "-o", str(solution_binary)],
            cwd=workspace,
            timeout=30,
        )

        scores: list[int] = []
        for input_path in sorted(cases_dir.glob("*.txt")):
            output_path = temp / f"{input_path.stem}.out"
            with input_path.open("r", encoding="utf-8") as source, output_path.open(
                "w", encoding="utf-8"
            ) as target:
                solution = subprocess.run(
                    [str(solution_binary)],
                    cwd=workspace,
                    stdin=source,
                    stdout=target,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5,
                )
            if solution.returncode != 0:
                raise RuntimeError(
                    f"solution failed on {input_path.name}: {(solution.stderr or '')[-2000:]}"
                )
            tested = run_checked(
                [str(workspace / "tools" / "bin" / "tester"), str(input_path), str(output_path)],
                cwd=workspace,
                timeout=5,
            )
            match = SCORE_PATTERN.search((tested.stdout or "") + (tested.stderr or ""))
            if match is None:
                raise RuntimeError(f"tester returned no Score line for {input_path.name}")
            scores.append(int(match.group(1)))

    if len(scores) != len(PUBLIC_SEEDS):
        raise RuntimeError(f"expected {len(PUBLIC_SEEDS)} public cases, evaluated {len(scores)}")
    return {
        "local_score_sum": sum(scores),
        "valid_cases": len(scores),
        "total_cases": len(PUBLIC_SEEDS),
        "per_case_scores": scores,
    }


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    try:
        print(json.dumps(evaluate(workspace), sort_keys=True))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
