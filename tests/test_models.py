from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from goal_plus.models import (
    AcceptanceViewSpec,
    AgentHostHandle,
    AgentSessionRecord,
    Budget,
    CandidateRecord,
    CandidateProposal,
    CandidateTask,
    EditSurface,
    GoalPlusSpecDraft,
    SearchPlan,
    SearchSpec,
    SearchSpecDraft,
    StrategySpec,
    VerifierCommand,
    WorkerBudget,
    ModelSpec,
)
from goal_plus.runtime import (
    FileSearchRuntime,
    SUPPLEMENTAL_EVALUATION_ENABLED_ENV,
    SUPPLEMENTAL_EVALUATION_REQUIRED_ENV,
)
from tests._runtime_helpers import make_project


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
    assert dumped["strategy"]["orchestration_mode"] == "parallel_loops"
    assert dumped["strategy"]["worker_host"] == "codex"
    assert "models" not in dumped["strategy"]


def test_search_spec_loads_legacy_non_gating_acceptance_view() -> None:
    data = valid_spec_dict()
    data["acceptance_view"] = {
        "rubric_name": "SWE issue coverage",
        "benchmark_context": "The process gate only checks for a valid patch.",
        "criteria": [
            {
                "id": "issue_requirements",
                "category": "issue_coverage",
                "description": "Cover each behavior requested by the issue.",
                "importance": "high",
                "evidence_hints": ["changed implementation", "focused tests"],
            },
            {
                "id": "regression_risk",
                "category": "regression",
                "description": "Preserve adjacent behavior and API compatibility.",
            },
        ],
    }

    spec = SearchSpec.model_validate(data)

    assert isinstance(spec.acceptance_view, AcceptanceViewSpec)
    assert spec.acceptance_view.affects_final_result is False
    assert spec.acceptance_view.tie_policy == "retain_latest"
    dumped = spec.model_dump(mode="json")["acceptance_view"]
    assert dumped["criteria"][0]["id"] == "issue_requirements"
    assert "required" not in dumped["criteria"][0]


def test_acceptance_view_rejects_gating_or_ambiguous_criteria() -> None:
    data = valid_spec_dict()
    data["acceptance_view"] = {
        "rubric_name": "invalid",
        "benchmark_context": "invalid contract",
        "affects_final_result": True,
        "criteria": [
            {
                "id": "coverage",
                "category": "coverage",
                "description": "Inspect coverage.",
                "required": True,
            }
        ],
    }
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)

    del data["acceptance_view"]["affects_final_result"]
    del data["acceptance_view"]["criteria"][0]["required"]
    data["acceptance_view"]["criteria"].append(
        dict(data["acceptance_view"]["criteria"][0])
    )
    with pytest.raises(ValidationError, match="ids must be unique"):
        SearchSpec.model_validate(data)


def test_new_freeze_rejects_legacy_acceptance_view(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    data = valid_spec_dict()
    data["source_path"] = str(project)
    data["acceptance_view"] = {
        "rubric_name": "SWE issue coverage",
        "benchmark_context": "The hard process metric is sparse.",
        "criteria": [
            {
                "id": "issue_requirements",
                "category": "issue_coverage",
                "description": "Cover each behavior requested by the issue.",
            }
        ],
    }
    with pytest.raises(ValueError, match="acceptance_view is retired"):
        FileSearchRuntime(tmp_path / ".gp").freeze_spec(
            SearchSpec.model_validate(data), [project / "evaluator.py"]
        )


def test_required_supplemental_evaluation_rejects_disabled_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    data = valid_spec_dict()
    data["source_path"] = str(project)
    monkeypatch.setenv(SUPPLEMENTAL_EVALUATION_ENABLED_ENV, "0")
    monkeypatch.setenv(SUPPLEMENTAL_EVALUATION_REQUIRED_ENV, "1")

    with pytest.raises(
        ValueError,
        match="requires GOAL_PLUS_SUPPLEMENTAL_EVALUATION_ENABLED=1",
    ):
        FileSearchRuntime(tmp_path / ".gp-missing").freeze_spec(
            SearchSpec.model_validate(data), [project / "evaluator.py"]
        )


def test_supplemental_evaluation_does_not_change_frozen_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    data = valid_spec_dict()
    data["source_path"] = str(project)
    monkeypatch.setenv(SUPPLEMENTAL_EVALUATION_ENABLED_ENV, "1")
    monkeypatch.setenv(SUPPLEMENTAL_EVALUATION_REQUIRED_ENV, "1")

    frozen = FileSearchRuntime(tmp_path / ".gp").freeze_spec(
        SearchSpec.model_validate(data), [project / "evaluator.py"]
    )

    assert frozen.spec.acceptance_view is None
    assert "supplemental_evaluation" not in frozen.spec.model_dump(mode="json")


def test_goal_plus_spec_draft_exposes_typed_partial_search_spec() -> None:
    draft = GoalPlusSpecDraft(
        baseline={},
        metric={"name": "combined_score"},
        correctness_gate={},
        edit_surface={},
        search_spec={
            "metric_name": "combined_score",
            "edit_surface": {"allow": ["solution.cpp"]},
            "process_verifiers": [
                {
                    "name": "public_score",
                    "role": "ranking_signal",
                    "command": ["python", "verify.py"],
                }
            ],
        },
        promotion_rule="highest public score",
        confidence="medium",
        open_questions=["Confirm the source path."],
    )

    assert isinstance(draft.search_spec, SearchSpecDraft)
    assert draft.search_spec.edit_surface is not None
    assert draft.search_spec.edit_surface.allow == ["solution.cpp"]
    assert draft.model_dump(mode="json")["search_spec"] == {
        "metric_name": "combined_score",
        "edit_surface": {
            "allow": ["solution.cpp"],
            "deny": [],
        },
        "process_verifiers": [
            {
                "name": "public_score",
                "role": "ranking_signal",
                "command": ["python", "verify.py"],
                "cwd": ".",
                "timeout_seconds": 300,
                "feedback_policy": "visible_to_workers",
                "expected_outputs": [],
            }
        ],
    }


def test_goal_plus_spec_draft_keeps_legacy_unstructured_search_spec_readable() -> None:
    draft = GoalPlusSpecDraft(
        baseline={},
        metric={},
        correctness_gate={},
        edit_surface={},
        search_spec={"allowed_paths": ["legacy.py"], "process_verifier": "old"},
        promotion_rule="legacy record",
        confidence="high",
    )

    assert isinstance(draft.search_spec, dict)
    assert draft.model_dump(mode="json")["search_spec"]["allowed_paths"] == [
        "legacy.py"
    ]


def test_expected_outputs_schema_describes_artifact_paths_not_stdout_parser() -> None:
    schema = VerifierCommand.model_json_schema()
    description = schema["properties"]["expected_outputs"]["description"]

    assert "产物路径或 glob" in description
    assert "不解析 verifier stdout metric" in description


def test_model_spec_requires_a_concrete_model_reference() -> None:
    with pytest.raises(ValidationError):
        ModelSpec(model="")

    model = ModelSpec(model="gpt", count=1)
    assert model.model_dump(mode="json") == {
        "model": "gpt",
        "count": 1,
        "provider": None,
        "adapter_version": None,
        "reasoning_effort": None,
        "service_tier": None,
        "context_policy": {},
    }


def test_verifier_resource_lock_rejects_blank_names() -> None:
    command = {
        "name": "score",
        "role": "ranking_signal",
        "command": ["python", "verify.py"],
        "resource_lock": "ascend-npu:0",
    }
    assert VerifierCommand.model_validate(command).resource_lock == "ascend-npu:0"
    command["resource_lock"] = "  ascend-npu:0  "
    assert VerifierCommand.model_validate(command).resource_lock == "ascend-npu:0"

    command["resource_lock"] = " "
    with pytest.raises(ValidationError, match="resource_lock must be non-empty"):
        VerifierCommand.model_validate(command)


def test_search_spec_supports_copy_and_git_worktree_workspace_backends() -> None:
    default_spec = SearchSpec.model_validate(valid_spec_dict())
    assert default_spec.workspace.backend == "git_worktree"
    assert default_spec.model_dump(mode="json")["workspace"] == {
        "backend": "git_worktree"
    }

    copy_data = valid_spec_dict()
    copy_data["workspace"] = {"backend": "copy"}
    copy_spec = SearchSpec.model_validate(copy_data)
    assert copy_spec.workspace.backend == "copy"

    invalid_data = valid_spec_dict()
    invalid_data["workspace"] = {"backend": "overlay"}
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(invalid_data)


def test_search_spec_requires_structured_strategy() -> None:
    data = valid_spec_dict()
    data["strategy"] = {
        "name": "agent_guided",
        "worker_host": "codex",
        "worker_agent_type": "search_candidate_agent",
    }
    spec = SearchSpec.model_validate(data)
    assert spec.strategy.name == "agent_guided"
    assert spec.strategy.worker_host == "codex"
    assert spec.strategy.worker_agent_type == "search_candidate_agent"

    legacy_string = valid_spec_dict()
    legacy_string["strategy"] = "evolve"
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(legacy_string)

    for retired_field, retired_value in (
        ("worker_mode", "agent-session-pool"),
        ("history_policy", {"scope": "top_n"}),
        ("driver", "builtin"),
        ("parent_policy", "best"),
    ):
        data = valid_spec_dict()
        data["strategy"] = {"name": "agent_guided", retired_field: retired_value}
        with pytest.raises(ValidationError):
            SearchSpec.model_validate(data)


def test_strategy_spec_accepts_supported_worker_hosts() -> None:
    assert StrategySpec(worker_host="codex").worker_host == "codex"
    assert StrategySpec(worker_host="pi-rpc").worker_host == "pi-rpc"

    with pytest.raises(ValidationError):
        StrategySpec(worker_host="unsupported")  # type: ignore[arg-type]


def test_strategy_spec_accepts_parallel_loop_orchestration() -> None:
    default = StrategySpec()
    parallel = StrategySpec(orchestration_mode="parallel_loops")

    assert default.orchestration_mode == "parallel_loops"
    assert parallel.orchestration_mode == "parallel_loops"

    with pytest.raises(ValidationError):
        StrategySpec(orchestration_mode="conductor")  # type: ignore[arg-type]


def test_strategy_spec_accepts_worker_budget() -> None:
    spec = StrategySpec(
        worker_host="codex",
        worker_budget={
            "min_runtime_seconds": 300,
            "min_verifier_runs": 2,
            "max_runtime_seconds": 600,
            "max_turns": 8,
            "on_exceed": "interrupt",
        },
    )

    assert isinstance(spec.worker_budget, WorkerBudget)
    assert spec.worker_budget.min_runtime_seconds == 300
    assert spec.worker_budget.min_verifier_runs == 2
    assert spec.worker_budget.max_runtime_seconds == 600
    assert spec.worker_budget.max_turns == 8
    assert spec.worker_budget.on_exceed == "interrupt"
    assert spec.model_dump(mode="json")["worker_budget"] == {
        "min_runtime_seconds": 300,
        "min_verifier_runs": 2,
        "max_runtime_seconds": 600,
        "max_turns": 8,
        "on_exceed": "interrupt",
    }


@pytest.mark.codex
def test_strategy_spec_accepts_codex_worker_launch_options() -> None:
    spec = StrategySpec(
        worker_host="codex",
        worker_launch={
            "model": "gpt-5.6-terra",
            "reasoning_effort": "high",
            "service_tier": "priority",
        },
    )

    assert spec.model_dump(mode="json")["worker_launch"] == {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "service_tier": "priority",
    }


def test_evidence_annotator_config_is_optional_and_overridable() -> None:
    inherited = StrategySpec()
    extended_timeout = StrategySpec(
        evidence_annotator={"timeout_seconds": 1800}
    )
    explicit = StrategySpec(
        evidence_annotator={
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
            "timeout_seconds": 90,
            "provider": {
                "base_url": "https://proxy.example/v1",
                "api_key_env": "ANNOTATOR_API_KEY",
            },
        }
    )
    pi_explicit = StrategySpec(
        worker_host="pi-rpc",
        evidence_annotator={
            "model": "deepseek-chat",
            "pi_provider": "deepseek",
        },
    )
    independent_codex = StrategySpec(
        worker_host="pi-rpc",
        evidence_annotator={
            "host": "codex",
            "model": "gpt-5.6-luna",
        },
    )

    assert inherited.model_dump(mode="json")["evidence_annotator"] == {
        "host": None,
        "model": None,
        "pi_provider": None,
        "reasoning_effort": None,
        "timeout_seconds": 1800,
        "provider": None,
    }
    assert extended_timeout.evidence_annotator.timeout_seconds == 1800
    with pytest.raises(ValidationError):
        StrategySpec(evidence_annotator={"timeout_seconds": 1801})
    assert explicit.model_dump(mode="json")["evidence_annotator"] == {
        "host": None,
        "model": "gpt-5.6-sol",
        "pi_provider": None,
        "reasoning_effort": "medium",
        "timeout_seconds": 90,
        "provider": {
            "provider_id": "goal-plus-evidence",
            "name": "Goal Plus Evidence provider",
            "base_url": "https://proxy.example/v1",
            "api_key_env": "ANNOTATOR_API_KEY",
            "wire_api": "responses",
        },
    }
    assert pi_explicit.model_dump(mode="json")["evidence_annotator"] == {
        "host": None,
        "model": "deepseek-chat",
        "pi_provider": "deepseek",
        "reasoning_effort": None,
        "timeout_seconds": 1800,
        "provider": None,
    }
    assert independent_codex.evidence_annotator.host == "codex"


def test_worker_budget_requires_runtime_or_turn_limit() -> None:
    with pytest.raises(ValidationError):
        WorkerBudget()

    with pytest.raises(ValidationError):
        WorkerBudget(max_runtime_seconds=0)

    with pytest.raises(ValidationError):
        WorkerBudget(max_turns=0)

    with pytest.raises(ValidationError, match="requires max_runtime_seconds"):
        WorkerBudget(min_runtime_seconds=300, max_turns=8)

    with pytest.raises(ValidationError, match="must be less than"):
        WorkerBudget(min_runtime_seconds=600, max_runtime_seconds=600)


def test_strategy_plan_models_capture_initial_independent_proposals() -> None:
    plan = SearchPlan(
        run_id="run_1",
        plan_id="plan_001",
        strategy=StrategySpec(name="agent_guided"),
        requested_k=4,
        planned_k=2,
        remaining_budget=2,
        requires_agent_proposals=True,
        created_at="2026-06-24T00:00:00Z",
    )
    proposal = CandidateProposal(
        intent="try an independent implementation",
        expected_tradeoff="higher score with more risk",
    )

    assert plan.requires_agent_proposals is True
    assert proposal.intent == "try an independent implementation"

    with pytest.raises(ValidationError):
        CandidateProposal(
            intent="mutate c001",
            parent_candidate_ids=["c001"],  # type: ignore[call-arg]
        )


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
            "agent_type": "search_candidate_agent",
            "description": "c001 try one direction",
            "prompt": "agent_session_id=agent_001; candidate_id=c001; idea: try one direction",
        },
        counters={"verifier_runs": 0},
    )
    assert session.candidate_id == "c001"
    assert session.host == "codex"
    assert session.host_handle == AgentHostHandle(host="codex")
    assert session.launch["agent_type"] == "search_candidate_agent"

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
    data["budget"]["max_parallel"] = 0
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
