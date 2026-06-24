from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_any_search_mcp.models import SearchSpec
from agentic_any_search_mcp.runtime import FileSearchRuntime
from agentic_any_search_mcp.tools import SearchTools


ROOT = Path(__file__).resolve().parents[1]


EXAMPLE_SPECS = [
    (
        "circle_packing_search_spec.json",
        "tests/fixtures/circle_packing/evaluator.py",
        "combined_score",
    ),
    (
        "signal_processing_search_spec.json",
        "tests/fixtures/signal_processing/evaluator.py",
        "overall_score",
    ),
]


def load_example_spec(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("spec_name", "verifier_path", "metric_name"), EXAMPLE_SPECS)
def test_two_round_example_specs_are_valid(
    spec_name: str,
    verifier_path: str,
    metric_name: str,
) -> None:
    spec = load_example_spec(spec_name)
    parsed = SearchSpec.model_validate(spec)

    assert parsed.metric_name == metric_name
    assert parsed.budget.max_candidates == 8
    assert parsed.budget.max_parallel == 4
    assert parsed.root_hypotheses == []
    assert parsed.constraints["suggested_batch_size"] == 4
    assert not Path(parsed.source_path).is_absolute()
    assert (ROOT / verifier_path).exists()


@pytest.mark.parametrize(("spec_name", "verifier_path", "metric_name"), EXAMPLE_SPECS)
def test_two_round_examples_create_batches_and_verify_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec_name: str,
    verifier_path: str,
    metric_name: str,
) -> None:
    monkeypatch.chdir(ROOT)
    tools = SearchTools(FileSearchRuntime(tmp_path / ".search"))
    spec = load_example_spec(spec_name)

    frozen = tools.search_freeze_spec(spec, [verifier_path])
    run_id = tools.search_create(frozen["frozen_spec_id"])["run_id"]

    first_round = tools.search_next_batch(run_id, 4)
    second_round = tools.search_next_batch(run_id, 4)

    assert [task["candidate_id"] for task in first_round] == ["c001", "c002", "c003", "c004"]
    assert [task["candidate_id"] for task in second_round] == ["c005", "c006", "c007", "c008"]
    assert first_round[0]["hypothesis"] == "Independent candidate c001"
    assert second_round[0]["hypothesis"] == "Independent candidate c005"

    for candidate_id in ("c001", "c005"):
        tools.search_submit_candidate(
            run_id,
            candidate_id,
            {
                "candidate_id": candidate_id,
                "status": "patch_ready",
                "summary": "baseline candidate",
            },
        )
        report = tools.search_run_verifier(run_id, candidate_id)

        assert report["process_passed"] is True
        assert report["aggregate_score"] is not None
        assert report["aggregate_score"] > 0.0
        assert report["verifier_results"][0]["metrics"][metric_name] == report["aggregate_score"]

    status = tools.search_status(run_id)
    assert status["candidates_total"] == 8
    assert status["candidates_evaluated"] == 2
