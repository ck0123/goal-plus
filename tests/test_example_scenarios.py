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
        {"max_candidates": 4, "max_parallel": 2, "worker_agent_type": "AnySearchAgentFlash"},
    ),
    (
        "signal_processing_search_spec.json",
        "tests/fixtures/signal_processing/evaluator.py",
        "overall_score",
        {"max_candidates": 8, "max_parallel": 4, "worker_agent_type": "AnySearchAgent"},
    ),
]

SEARCH_MODE_SPECS = [
    (
        "search-mode/k_module_adaptevolve_search_spec.json",
        "adaptevolve",
        "python",
        {"max_candidates": 1, "max_parallel": 1, "worker_agent_type": "AnySearchAgentFlash"},
    ),
    (
        "search-mode/k_module_openevolve_search_spec.json",
        "openevolve",
        "builtin",
        {"max_candidates": 2, "max_parallel": 1, "worker_agent_type": "AnySearchAgentFlash"},
    ),
]


def load_expected(spec_name: str) -> dict:
    for name, _, _, expected in EXAMPLE_SPECS:
        if name == spec_name:
            return expected
    raise KeyError(spec_name)


def load_example_spec(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("spec_name", "strategy_name", "driver", "expected"), SEARCH_MODE_SPECS)
def test_search_mode_example_specs_are_valid(
    spec_name: str,
    strategy_name: str,
    driver: str,
    expected: dict,
) -> None:
    spec = load_example_spec(spec_name)
    parsed = SearchSpec.model_validate(spec)

    assert parsed.source_path == "tests/fixtures/k_module_problem"
    assert parsed.metric_name == "combined_score"
    assert parsed.strategy.name == strategy_name
    assert parsed.strategy.driver == driver
    assert parsed.strategy.worker_mode == "agent-session-pool"
    assert parsed.strategy.worker_agent_type == expected["worker_agent_type"]
    assert parsed.budget.max_candidates == expected["max_candidates"]
    assert parsed.budget.max_parallel == expected["max_parallel"]
    assert not Path(parsed.source_path).is_absolute()


@pytest.mark.parametrize(("spec_name", "verifier_path", "metric_name", "expected"), EXAMPLE_SPECS)
def test_two_round_example_specs_are_valid(
    spec_name: str,
    verifier_path: str,
    metric_name: str,
    expected: dict,
) -> None:
    spec = load_example_spec(spec_name)
    parsed = SearchSpec.model_validate(spec)

    assert parsed.metric_name == metric_name
    assert parsed.budget.max_candidates == expected["max_candidates"]
    assert parsed.budget.max_parallel == expected["max_parallel"]
    assert parsed.root_hypotheses == []
    assert parsed.constraints["suggested_batch_size"] == 4
    assert parsed.strategy.worker_mode == "agent-session-pool"
    assert parsed.strategy.worker_agent_type == expected["worker_agent_type"]
    assert not Path(parsed.source_path).is_absolute()
    assert (ROOT / verifier_path).exists()


@pytest.mark.parametrize(("spec_name", "verifier_path", "metric_name", "expected"), EXAMPLE_SPECS)
def test_two_round_examples_create_batches_and_verify_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec_name: str,
    verifier_path: str,
    metric_name: str,
    expected: dict,
) -> None:
    monkeypatch.chdir(ROOT)
    tools = SearchTools(FileSearchRuntime(tmp_path / ".search"))
    spec = load_example_spec(spec_name)

    frozen = tools.search_freeze_spec(spec, [verifier_path])
    run_id = tools.search_create(frozen["frozen_spec_id"])["run_id"]

    first_plan = tools.search_plan_next(run_id, 4)
    first_round = tools.search_start_batch(run_id, first_plan["plan_id"])
    second_plan = tools.search_plan_next(run_id, 4)
    second_round = tools.search_start_batch(run_id, second_plan["plan_id"])

    first_expected = [
        f"c{index:03d}" for index in range(1, expected["max_parallel"] + 1)
    ]
    second_expected = [
        f"c{index:03d}"
        for index in range(expected["max_parallel"] + 1, expected["max_candidates"] + 1)
    ]

    assert first_plan["planned_k"] == expected["max_parallel"]
    assert second_plan["planned_k"] == expected["max_parallel"]
    assert [task["candidate_id"] for task in first_round] == first_expected
    assert [task["candidate_id"] for task in second_round] == second_expected
    assert first_round[0]["hypothesis"] == "Independent candidate c001"

    for candidate_id in ("c001", "c002"):
        session = tools.search_start_agent_session(
            run_id,
            candidate_id,
            {"goal": f"verify baseline fixture path for {candidate_id}"},
        )
        tools.search_get_agent_context(session["agent_session_id"])
        report = tools.search_run_verifier(
            run_id,
            candidate_id,
            agent_session_id=session["agent_session_id"],
        )

        assert report["process_passed"] is True
        assert report["aggregate_score"] is not None
        assert report["aggregate_score"] > 0.0
        assert report["verifier_results"][0]["metrics"][metric_name] == report["aggregate_score"]

    history = tools.search_list_history(run_id)
    assert history["total_candidates"] == expected["max_candidates"]
    assert history["returned_candidates"] == min(5, expected["max_candidates"])
    assert history["sort_by"] == "score"
    assert history["candidates"][0]["score"] >= history["candidates"][1]["score"]
    assert metric_name in history["candidates"][0]["key_metrics"]

    created_history = tools.search_list_history(run_id, top_n=2, sort_by="created")
    assert [item["candidate_id"] for item in created_history["candidates"]] == ["c001", "c002"]

    status = tools.search_status(run_id)
    assert status["candidates_total"] == expected["max_candidates"]
    assert status["candidates_evaluated"] == 2
