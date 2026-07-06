from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from agentic_any_search_mcp.models import (
    AgentHostHandle,
    AgentSessionRecord,
    CandidateProposal,
    CandidateTask,
    FrozenSpec,
    GoalPlusGateResult,
    GoalPlusNextAction,
    GoalPlusRecord,
    RunState,
    RunSummary,
    ScoreReport,
    SearchPlan,
    SearchSpec,
    StrategySpec,
    VerifierResult,
    VerifierRole,
)
from agentic_any_search_mcp.tools import GoalPlusTools, SearchTools


def spec_dict() -> dict:
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
        },
        "process_verifiers": [
            {
                "name": "score",
                "role": "ranking_signal",
                "command": ["python", "evaluator.py"],
            }
        ],
    }


def frozen_spec() -> FrozenSpec:
    spec = SearchSpec.model_validate(spec_dict())
    return FrozenSpec(
        frozen_spec_id="spec_123",
        spec_hash="hash",
        spec=spec,
        verifier_hashes={"evaluator.py": "abc"},
        frozen_verifier_paths={"evaluator.py": "/tmp/evaluator.py"},
        created_at="2026-06-24T00:00:00Z",
    )


def goal_plus_record() -> GoalPlusRecord:
    return GoalPlusRecord(
        goal_plus_id="gp_0001",
        raw_goal="Optimize a benchmark if possible",
        status="active",
        phase="intake",
        next_action=GoalPlusNextAction(
            kind="record_triage",
            description="classify the goal",
        ),
        created_at="2026-07-06T00:00:00Z",
        updated_at="2026-07-06T00:00:00Z",
    )


def test_search_freeze_spec_converts_input_and_serializes_output() -> None:
    runtime = Mock()
    runtime.freeze_spec.return_value = frozen_spec()
    tools = SearchTools(runtime)

    result = tools.search_freeze_spec(spec_dict(), ["evaluator.py"])

    spec_arg, paths_arg = runtime.freeze_spec.call_args.args
    assert isinstance(spec_arg, SearchSpec)
    assert paths_arg == [Path("evaluator.py")]
    assert result["frozen_spec_id"] == "spec_123"
    assert result["spec"]["process_verifiers"][0]["role"] == "ranking_signal"


def test_search_tools_delegate_runtime_calls_with_models() -> None:
    runtime = Mock()
    agent_session = AgentSessionRecord(
        agent_session_id="agent_001",
        run_id="run_1",
        candidate_id="c001",
        created_at="2026-06-24T00:00:00Z",
        updated_at="2026-06-24T00:00:00Z",
        workspace=Path("/tmp/c001"),
        launch={
            "subagent_type": "AnySearchAgent",
            "description": "c001 try one",
            "prompt": "agent_session_id=agent_001; candidate_id=c001; idea: try one",
        },
    )
    runtime.create_run.return_value = "run_1"
    runtime.status.return_value = RunSummary(
        run_id="run_1",
        state=RunState.RUNNING,
        frozen_spec_id="spec_123",
        candidates_total=0,
        candidates_evaluated=0,
    )
    runtime.list_history.return_value = {
        "run_id": "run_1",
        "candidates": [],
    }
    runtime.plan_next.return_value = SearchPlan(
        run_id="run_1",
        plan_id="plan_001",
        strategy=StrategySpec(),
        requested_k=1,
        planned_k=1,
        remaining_budget=4,
        created_at="2026-06-24T00:00:00Z",
    )
    runtime.start_batch.return_value = [
        CandidateTask(
            run_id="run_1",
            candidate_id="c001",
            hypothesis="try",
            workspace=Path("/tmp/c001"),
            allowed_files=["initial_program.py"],
            denied_files=["evaluator.py"],
        )
    ]
    runtime.start_agent_session.return_value = agent_session
    generic_bound_session = agent_session.model_copy(
        update={
            "host": "codex",
            "host_handle": AgentHostHandle(host="codex", task_name="search_agent_001"),
        }
    )
    bound_session = agent_session.model_copy(
        update={"opencode_session_id": "opencode_session_001"}
    )
    continued_session = bound_session.model_copy(
        update={
            "launch": {
                "task_id": "opencode_session_001",
                "subagent_type": "AnySearchAgent",
                "description": "c001 continue try one",
                "prompt": "continue_existing_agent_session=true; agent_session_id=agent_001",
            }
        }
    )
    runtime.bind_agent_handle.return_value = generic_bound_session
    runtime.bind_opencode_session.return_value = bound_session
    runtime.continue_agent_session.return_value = continued_session
    runtime.get_agent_context.return_value = {"agent_session_id": "agent_001"}
    runtime.run_verifier.return_value = ScoreReport(
        run_id="run_1",
        candidate_id="c001",
        validity_passed=True,
        process_passed=True,
        aggregate_score=1.0,
        verifier_results=[
            VerifierResult(
                name="score",
                role=VerifierRole.RANKING_SIGNAL,
                passed=True,
                score=1.0,
            )
        ],
    )
    runtime.list_iterations.return_value = [
        {"iteration": 1, "score": 0.4, "agent_session_id": "agent_001"},
        {"iteration": 2, "score": 0.7, "agent_session_id": "agent_001"},
    ]
    runtime.select.return_value = {"selected_candidate_id": "c001"}
    runtime.report.return_value = Path("/tmp/report.md")
    runtime.promote.return_value = Path("/tmp/c001.patch")
    tools = SearchTools(runtime)

    assert tools.search_create("spec_123") == {"run_id": "run_1"}
    assert tools.search_status("run_1")["state"] == "running"
    assert tools.search_list_history("run_1", top_n=3, sort_by="created") == {
        "run_id": "run_1",
        "candidates": [],
    }
    assert tools.search_plan_next("run_1", requested_k=1)["plan_id"] == "plan_001"
    assert tools.search_start_batch(
        "run_1",
        "plan_001",
        [{"intent": "derive from official history", "parent_candidate_ids": ["c001"]}],
    )[0]["candidate_id"] == "c001"
    assert tools.search_start_agent_session(
        "run_1",
        "c001",
        {"goal": "try one"},
    )["agent_session_id"] == "agent_001"
    assert tools.search_bind_agent_handle(
        "agent_001",
        {"host": "codex", "task_name": "search_agent_001"},
    )["host_handle"]["task_name"] == "search_agent_001"
    assert tools.search_bind_opencode_session(
        "agent_001",
        "opencode_session_001",
    )["opencode_session_id"] == "opencode_session_001"
    continued = tools.search_continue_agent_session(
        "agent_001",
        {"goal": "continue same node"},
    )
    assert continued["launch"]["task_id"] == "opencode_session_001"
    assert tools.search_get_agent_context("agent_001") == {"agent_session_id": "agent_001"}
    assert tools.search_run_verifier("run_1", "c001")["aggregate_score"] == 1.0
    assert tools.search_run_verifier(
        "run_1", "c001", agent_session_id="agent_001"
    )["aggregate_score"] == 1.0
    runtime.run_verifier.assert_called_with(
        "run_1",
        "c001",
        scope="process",
        agent_session_id="agent_001",
    )
    iterations = tools.search_list_iterations("run_1", "c001")
    assert len(iterations) == 2
    assert iterations[0]["iteration"] == 1
    assert iterations[1]["score"] == 0.7
    runtime.list_iterations.assert_called_once_with("run_1", "c001")
    assert tools.search_select("run_1") == {"selected_candidate_id": "c001"}
    assert tools.search_report("run_1") == {"report_path": "/tmp/report.md"}
    assert tools.search_promote("run_1", "c001") == {"artifact_path": "/tmp/c001.patch"}

    proposal_arg = runtime.start_batch.call_args.args[2][0]
    assert isinstance(proposal_arg, CandidateProposal)
    runtime.list_history.assert_called_once_with("run_1", top_n=3, sort_by="created")
    runtime.plan_next.assert_called_once_with("run_1", requested_k=1)
    runtime.start_agent_session.assert_called_once_with(
        run_id="run_1",
        candidate_id="c001",
        directive={"goal": "try one"},
    )
    runtime.bind_opencode_session.assert_called_once_with(
        agent_session_id="agent_001",
        opencode_session_id="opencode_session_001",
    )
    runtime.bind_agent_handle.assert_called_once_with(
        agent_session_id="agent_001",
        handle={"host": "codex", "task_name": "search_agent_001"},
    )
    runtime.continue_agent_session.assert_called_once_with(
        agent_session_id="agent_001",
        directive={"goal": "continue same node"},
    )


def test_search_tools_expose_no_lifecycle_methods() -> None:
    runtime = Mock()
    tools = SearchTools(runtime)
    for deleted in (
        "search_update_agent_status",
        "search_list_agent_status",
        "search_finish_agent_session",
        "search_abort_agent_session",
        "search_abort_all_agent_sessions",
        "search_publish_observation",
        "search_list_observations",
        "search_wait_agent_events",
        "search_submit_candidate",
    ):
        assert not hasattr(tools, deleted), f"SearchTools should not expose {deleted}"


def test_goal_plus_tools_delegate_runtime_calls_with_models() -> None:
    runtime = Mock()
    record = goal_plus_record()
    runtime.create_goal.return_value = record
    runtime.status.return_value = record
    runtime.list_events.return_value = [{"event_type": "created"}]
    runtime.record_triage.return_value = record
    runtime.save_spec_draft.return_value = record
    runtime.confirm_frozen_verifier.return_value = record
    runtime.link_search_run.return_value = record
    runtime.record_search_result.return_value = record
    runtime.set_status.return_value = record.model_copy(update={"status": "complete"})
    runtime.gate.return_value = GoalPlusGateResult(
        decision="block",
        phase="intake",
        status="active",
        reason="needs triage",
        continuation_prompt="continue triage",
    )
    tools = GoalPlusTools(runtime)

    created = tools.goal_plus_create(
        raw_goal="Optimize a benchmark if possible",
        source_path=".",
        policy={"max_discovery_turns": 1},
    )
    assert created["goal_plus_id"] == "gp_0001"
    assert tools.goal_plus_status("gp_0001")["evidence_log"][0]["event_type"] == "created"
    assert tools.goal_plus_record_triage(
        "gp_0001",
        {
            "is_optimization": True,
            "confidence": "medium",
            "recommended_phase": "spec_discovery",
        },
    )["phase"] == "intake"
    assert tools.goal_plus_save_spec_draft(
        "gp_0001",
        {
            "baseline": {},
            "metric": {},
            "correctness_gate": {},
            "edit_surface": {},
            "search_spec": {},
            "promotion_rule": "must pass",
            "confidence": "high",
        },
    )["goal_plus_id"] == "gp_0001"
    assert tools.goal_plus_confirm_frozen_verifier(
        "gp_0001",
        confirmed_by="user",
        evidence={"message": "freeze it"},
    )["goal_plus_id"] == "gp_0001"
    assert tools.goal_plus_link_search_run("gp_0001", "spec_1", "run_1")["goal_plus_id"] == "gp_0001"
    assert tools.goal_plus_record_search_result(
        "gp_0001",
        run_id="run_1",
        selected_candidate_id="c001",
        report_path="/tmp/report.md",
        promotion_artifact_path="/tmp/c001.patch",
        summary="c001 selected",
    )["goal_plus_id"] == "gp_0001"
    assert tools.goal_plus_set_status(
        "gp_0001",
        status="complete",
        reason="done",
        evidence=[{"kind": "test"}],
    )["status"] == "complete"
    assert tools.goal_plus_gate(
        "gp_0001",
        event="stop",
        context={"turn_id": "turn_1"},
    )["continuation_prompt"] == "continue triage"

    runtime.create_goal.assert_called_once_with(
        raw_goal="Optimize a benchmark if possible",
        source_path=".",
        policy={"max_discovery_turns": 1},
    )
    runtime.confirm_frozen_verifier.assert_called_once_with(
        "gp_0001",
        confirmed_by="user",
        evidence={"message": "freeze it"},
    )
    runtime.link_search_run.assert_called_once_with("gp_0001", "spec_1", "run_1")
