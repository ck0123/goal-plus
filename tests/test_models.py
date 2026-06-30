from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_any_search_mcp.models import (
    AgentSessionRecord,
    Budget,
    CandidateRecord,
    CandidateProposal,
    CandidateTask,
    EditSurface,
    SearchPlan,
    SearchSpec,
    StrategySpec,
    VerifierCommand,
)


def valid_spec_dict() -> dict:
    return {
        "objective": "maximize toy score",
        "metric_name": "combined_score",
        "metric_direction": "maximize",
        "source_path": ".",
        "edit_surface": {
            "allow": ["initial_program.py"],
            "deny": ["evaluator.py"]},
        "budget": {
            "max_candidates": 4,
            "max_parallel": 2},
        "process_verifiers": [
            {
                "name": "score",
                "role": "ranking_signal",
                "command": ["python", "evaluator.py"]}
        ]}


def test_search_spec_parses_nested_models_and_serializes_enums() -> None:
    spec = SearchSpec.model_validate(valid_spec_dict())

    assert isinstance(spec.budget, Budget)
    assert isinstance(spec.edit_surface, EditSurface)
    assert isinstance(spec.process_verifiers[0], VerifierCommand)
    assert isinstance(spec.strategy, StrategySpec)

    dumped = spec.model_dump(mode="json")
    assert dumped["process_verifiers"][0]["role"] == "ranking_signal"
    assert dumped["metric_direction"] == "maximize"
    assert dumped["strategy"]["name"] == "agent_guided"
    assert dumped["strategy"]["worker_mode"] == "agent-session-pool"


def test_search_spec_requires_structured_strategy() -> None:
    data = valid_spec_dict()
    data["strategy"] = {"name": "agent_guided", "history_policy": {"scope": "top_n", "top_n": 3}}
    spec = SearchSpec.model_validate(data)
    assert spec.strategy.name == "agent_guided"
    assert spec.strategy.history_policy.top_n == 3
    assert spec.strategy.worker_mode == "agent-session-pool"

    data = valid_spec_dict()
    data["strategy"] = {
        "name": "independent_branches",
        "worker_mode": "agent-session-pool",
        "worker_agent_type": "AnySearchAgent"}
    spec = SearchSpec.model_validate(data)
    assert spec.strategy.worker_mode == "agent-session-pool"
    assert spec.strategy.worker_agent_type == "AnySearchAgent"

    legacy_string = valid_spec_dict()
    legacy_string["strategy"] = "evolve"
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(legacy_string)

    for retired in ("sub-agent-search-dispatch", "main-agent-search-direct", "auto"):
        data = valid_spec_dict()
        data["strategy"] = {"name": "independent_branches", "worker_mode": retired}
        with pytest.raises(ValidationError):
            SearchSpec.model_validate(data)


def test_strategy_plan_models_capture_proposal_contract() -> None:
    plan = SearchPlan(
        run_id="run_1",
        plan_id="plan_001",
        strategy=StrategySpec(name="agent_guided"),
        requested_k=4,
        planned_k=2,
        remaining_budget=2,
        requires_agent_proposals=True,
        proposal_contract={"count": 2, "must_reference_one_of": ["c001"]},  # type: ignore[arg-type]
        created_at="2026-06-24T00:00:00Z",
    )
    proposal = CandidateProposal(
        parent_candidate_ids=["c001"],
        intent="mutate c001",
        expected_tradeoff="higher score with more risk",
    )

    assert plan.proposal_contract.count == 2  # type: ignore[union-attr]
    assert proposal.parent_candidate_ids == ["c001"]


def test_agent_session_record_is_context_handle_with_required_candidate() -> None:
    session = AgentSessionRecord(
        agent_session_id="agent_001",
        run_id="run_1",
        candidate_id="c001",
        created_at="2026-06-24T00:00:00Z",
        updated_at="2026-06-24T00:00:00Z",
        workspace=Path("/tmp/c001"),
        directive={"goal": "try one direction"},
        launch={
            "subagent_type": "AnySearchAgent",
            "description": "c001 try one direction",
            "prompt": "agent_session_id=agent_001; candidate_id=c001; idea: try one direction",
            "background_required": False,
        },
        counters={"verifier_runs": 0},
    )
    assert session.candidate_id == "c001"
    assert session.launch["subagent_type"] == "AnySearchAgent"

    # candidate_id is now required - a subagent session without a candidate
    # has no useful role in this runtime.
    with pytest.raises(ValidationError):
        AgentSessionRecord(  # type: ignore[call-arg]
            agent_session_id="agent_002",
            run_id="run_1",
            created_at="2026-06-24T00:00:00Z",
            updated_at="2026-06-24T00:00:00Z",
            workspace=Path("/tmp/c001"),
        )


def test_search_spec_rejects_invalid_budget_and_blank_source_path() -> None:
    data = valid_spec_dict()
    data["budget"]["max_candidates"] = 0
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)

    data = valid_spec_dict()
    data["source_path"] = "   "
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


def test_models_reject_extra_fields() -> None:
    data = valid_spec_dict()
    data["unexpected"] = True

    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


def test_candidate_record_rejects_submitted_status() -> None:
    task = CandidateTask(
        run_id="run_1",
        candidate_id="c001",
        hypothesis="try one",
        workspace=Path("/tmp/c001"),
        allowed_files=["initial_program.py"],
        denied_files=["evaluator.py"],
    )

    with pytest.raises(ValidationError):
        CandidateRecord(
            candidate_id="c001",
            status="submitted",  # type: ignore[arg-type]
            task=task,
        )


def test_candidate_record_accepts_created_and_evaluated() -> None:
    task = CandidateTask(
        run_id="run_1",
        candidate_id="c001",
        hypothesis="try one",
        workspace=Path("/tmp/c001"),
        allowed_files=["initial_program.py"],
        denied_files=["evaluator.py"],
    )

    for status in ("created", "evaluated", "failed"):
        CandidateRecord(
            candidate_id="c001",
            status=status,
            task=task,
        )
