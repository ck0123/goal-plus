from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_any_search_mcp.models import (
    ArtifactBundle,
    Budget,
    CandidateRecord,
    CandidateTask,
    EditSurface,
    SearchSpec,
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
            "deny": ["evaluator.py"],
        },
        "budget": {
            "max_candidates": 4,
            "max_parallel": 2,
            "wall_clock_seconds": 300,
        },
        "process_verifiers": [
            {
                "name": "score",
                "role": "ranking_signal",
                "command": ["python", "evaluator.py"],
            }
        ],
    }


def test_search_spec_parses_nested_models_and_serializes_enums() -> None:
    spec = SearchSpec.model_validate(valid_spec_dict())

    assert isinstance(spec.budget, Budget)
    assert isinstance(spec.edit_surface, EditSurface)
    assert isinstance(spec.process_verifiers[0], VerifierCommand)

    dumped = spec.model_dump(mode="json")
    assert dumped["process_verifiers"][0]["role"] == "ranking_signal"
    assert dumped["metric_direction"] == "maximize"


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


def test_candidate_record_requires_artifact_candidate_match() -> None:
    task = CandidateTask(
        run_id="run_1",
        candidate_id="c001",
        hypothesis="try one",
        workspace=Path("/tmp/c001"),
        allowed_files=["initial_program.py"],
        denied_files=["evaluator.py"],
    )
    artifact = ArtifactBundle(candidate_id="c002", status="patch_ready")

    with pytest.raises(ValidationError):
        CandidateRecord(
            candidate_id="c001",
            status="submitted",
            task=task,
            artifact=artifact,
        )

