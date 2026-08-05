from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from goal_plus.models import (
    AgentHostHandle,
    AgentSessionRecord,
    Budget,
    CandidateRecord,
    CandidateProposal,
    CandidateTask,
    EditSurface,
    GoalPlusSpecDraft,
    IterationRecord,
    SearchPlan,
    SearchSpec,
    SearchSpecDraft,
    StrategySpec,
    VerifierCommand,
    WorkerBudget,
    ModelSpec,
)


def test_legacy_iteration_infers_shared_tool_publish_status() -> None:
    asset = {
        "asset_id": "legacy-asset",
        "candidate_id": "c001",
        "iteration": 1,
        "snapshot_hash": "abc123",
        "name": "legacy helper",
        "source_relative_path": "legacy-helper",
        "read_only_path": "/tmp/legacy-helper",
        "files": ["helper.py"],
        "size_bytes": 12,
        "created_at": "2026-08-03T00:00:00Z",
    }

    published = IterationRecord.model_validate(
        {
            "iteration": 1,
            "shared_tools": [asset],
            "created_at": "2026-08-03T00:00:00Z",
        }
    )
    partial = IterationRecord.model_validate(
        {
            "iteration": 1,
            "shared_tools": [asset],
            "shared_tool_errors": ["second asset rejected"],
            "created_at": "2026-08-03T00:00:00Z",
        }
    )
    unknown = IterationRecord.model_validate(
        {"iteration": 1, "created_at": "2026-08-03T00:00:00Z"}
    )

    assert published.shared_tool_publish_status == "published"
    assert partial.shared_tool_publish_status == "partially_published"
    assert unknown.shared_tool_publish_status == "legacy_unknown"


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

    assert inherited.model_dump(mode="json")["evidence_annotator"] == {
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
        "model": "deepseek-chat",
        "pi_provider": "deepseek",
        "reasoning_effort": None,
        "timeout_seconds": 1800,
        "provider": None,
    }


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


def test_shared_dir_is_opt_in_and_bounded() -> None:
    default_spec = SearchSpec.model_validate(valid_spec_dict())
    assert default_spec.shared_dir.enabled is False

    data = valid_spec_dict()
    data["shared_dir"] = {
        "enabled": True,
        "max_tools_per_iteration": 4,
        "max_files_per_iteration": 12,
        "max_path_entries_per_iteration": 96,
        "max_depth": 5,
        "max_bytes_per_iteration": 4096,
    }
    spec = SearchSpec.model_validate(data)
    assert spec.shared_dir.enabled is True
    assert spec.shared_dir.max_tools_per_iteration == 4
    assert spec.shared_dir.max_files_per_iteration == 12
    assert spec.shared_dir.max_path_entries_per_iteration == 96
    assert spec.shared_dir.max_depth == 5
    assert spec.shared_dir.max_bytes_per_iteration == 4096

    data["shared_dir"]["max_files_per_iteration"] = 0
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)

    data["shared_dir"]["max_files_per_iteration"] = 12
    data["shared_dir"]["max_depth"] = 0
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
