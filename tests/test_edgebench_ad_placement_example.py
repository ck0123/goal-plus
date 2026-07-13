from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from goal_plus.models import SearchSpec


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "examples" / "edgebench-ad-placement" / "workspace"
GENERATOR = WORKSPACE / "tools" / "bin" / "gen"
TESTER = WORKSPACE / "tools" / "bin" / "tester"
VERIFIER = WORKSPACE / ".goal-plus-verifiers" / "ad_local_score.py"


def _one_cell_output(input_path: Path) -> str:
    values = [int(token) for token in input_path.read_text(encoding="utf-8").split()]
    n = values[0]
    return "".join(
        f"{values[1 + 3 * index]} {values[2 + 3 * index]} "
        f"{values[1 + 3 * index] + 1} {values[2 + 3 * index] + 1}\n"
        for index in range(n)
    )


def test_public_generator_matches_edgebench_seed_file_and_output_contract(
    tmp_path: Path,
) -> None:
    seeds = tmp_path / "seeds.txt"
    cases = tmp_path / "cases"
    seeds.write_text("7\n11\n", encoding="utf-8")

    completed = subprocess.run(
        [str(GENERATOR), str(seeds), "-d", str(cases)],
        cwd=WORKSPACE,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert [path.name for path in sorted(cases.glob("*.txt"))] == [
        "0000.txt",
        "0001.txt",
    ]
    values = [int(token) for token in (cases / "0000.txt").read_text().split()]
    assert values[0] == 200
    assert len(values) == 1 + 3 * values[0]


def test_public_tester_matches_edgebench_cli_and_score_line_contract(
    tmp_path: Path,
) -> None:
    seeds = tmp_path / "seeds.txt"
    cases = tmp_path / "cases"
    output = tmp_path / "output.txt"
    seeds.write_text("19\n", encoding="utf-8")
    subprocess.run(
        [str(GENERATOR), str(seeds), "-d", str(cases)],
        cwd=WORKSPACE,
        check=True,
    )
    output.write_text(_one_cell_output(cases / "0000.txt"), encoding="utf-8")

    completed = subprocess.run(
        [str(TESTER), str(cases / "0000.txt"), str(output)],
        cwd=WORKSPACE,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert re.fullmatch(r"Score = \d+\n", completed.stderr)


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ is required")
def test_public_verifier_compiles_cpp_and_emits_goal_plus_json_metric() -> None:
    completed = subprocess.run(
        [sys.executable, str(VERIFIER)],
        cwd=WORKSPACE,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert completed.returncode == 0, completed.stderr
    metrics = json.loads(completed.stdout)
    assert metrics["local_score_sum"] > 0
    assert metrics["valid_cases"] == 10
    assert metrics["total_cases"] == 10
    assert len(metrics["per_case_scores"]) == 10


def test_search_spec_matches_edgebench_submission_surface_and_public_tools() -> None:
    data = json.loads(
        (ROOT / "examples" / "edgebench_ad_placement_search_spec.json").read_text(
            encoding="utf-8"
        )
    )
    spec = SearchSpec.model_validate(data)

    assert spec.source_path == "examples/edgebench-ad-placement/workspace"
    assert spec.edit_surface.allow == ["solution.cpp"]
    assert spec.metric_name == "local_score_sum"
    assert spec.metric_direction == "maximize"
    assert spec.process_verifiers[0].command == [
        "python",
        ".goal-plus-verifiers/ad_local_score.py",
    ]
    assert spec.promotion_verifiers == []
    assert "CPU-only/no-internet" in spec.constraints["edgebench_alignment"]
    assert "1 GB" in spec.constraints["edgebench_alignment"]
