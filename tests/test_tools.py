from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from agentic_any_search_mcp.models import (
    AgentObservation,
    AgentSessionBudget,
    AgentSessionEvent,
    AgentSessionRecord,
    AgentSessionWaitResult,
    ArtifactBundle,
    CandidateProposal,
    CandidateTask,
    FrozenSpec,
    RunState,
    RunSummary,
    ScoreReport,
    SearchPlan,
    SearchSpec,
    StrategySpec,
    VerifierResult,
)
from agentic_any_search_mcp.tools import SearchTools


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
    agent_budget = AgentSessionBudget(max_wall_seconds=120, deadline_at="2026-06-24T00:02:00Z")
    agent_session = AgentSessionRecord(
        agent_session_id="agent_001",
        run_id="run_1",
        candidate_id="c001",
        created_at="2026-06-24T00:00:00Z",
        updated_at="2026-06-24T00:00:00Z",
        last_heartbeat_at="2026-06-24T00:00:00Z",
        budget=agent_budget,
    )
    runtime.create_run.return_value = "run_1"
    runtime.status.return_value = RunSummary(
        run_id="run_1",
        state=RunState.RUNNING,
        frozen_spec_id="spec_123",
        candidates_total=0,
        candidates_running=0,
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
    runtime.next_batch.return_value = [
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
    runtime.get_agent_context.return_value = {"agent_session_id": "agent_001"}
    runtime.update_agent_status.return_value = agent_session.model_copy(update={"phase": "implementing"})
    runtime.list_agent_status.return_value = [agent_session]
    runtime.finish_agent_session.return_value = agent_session.model_copy(update={"status": "completed"})
    runtime.request_agent_finalize.return_value = agent_session.model_copy(update={"status": "finalizing"})
    runtime.abort_agent_session.return_value = agent_session.model_copy(update={"status": "aborted"})
    runtime.abort_all_agent_sessions.return_value = [agent_session.model_copy(update={"status": "aborted"})]
    runtime.record_agent_step.return_value = agent_session.model_copy(update={"counters": {"steps": 1}})
    runtime.publish_observation.return_value = AgentObservation(
        observation_id="obs_000001",
        run_id="run_1",
        agent_session_id="agent_001",
        created_at="2026-06-24T00:00:01Z",
        summary="found one issue",
    )
    runtime.list_observations.return_value = [
        {
            "observation_id": "obs_000001",
            "summary": "found one issue",
        }
    ]
    runtime.wait_agent_events.return_value = AgentSessionWaitResult(
        run_id="run_1",
        timed_out=False,
        events=[
            AgentSessionEvent(
                event_id="event_000001",
                run_id="run_1",
                agent_session_id="agent_001",
                type="agent_completed",
                created_at="2026-06-24T00:00:02Z",
            )
        ],
        sessions=[agent_session],
        active_count=0,
        max_concurrent_agents=2,
    )
    runtime.run_verifier.return_value = ScoreReport(
        run_id="run_1",
        candidate_id="c001",
        validity_passed=True,
        process_passed=True,
        aggregate_score=1.0,
        verifier_results=[
            VerifierResult(
                name="score",
                role="ranking_signal",
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
    assert tools.search_next_batch("run_1", 1)[0]["candidate_id"] == "c001"
    assert tools.search_start_agent_session(
        "run_1",
        "c001",
        {"goal": "try one"},
        {"max_wall_seconds": 120},
    )["agent_session_id"] == "agent_001"
    assert tools.search_get_agent_context("agent_001") == {"agent_session_id": "agent_001"}
    assert tools.search_update_agent_status(
        "agent_001",
        "implementing",
        current_goal="patch",
    )["phase"] == "implementing"
    assert tools.search_list_agent_status("run_1")[0]["agent_session_id"] == "agent_001"
    assert tools.search_finish_agent_session("agent_001")["status"] == "completed"
    assert tools.search_request_agent_finalize("agent_001", "deadline")["status"] == "finalizing"
    assert tools.search_abort_agent_session("agent_001", "stop")["status"] == "aborted"
    assert tools.search_abort_all_agent_sessions("run_1", "stop")["aborted"] == 1
    assert tools.search_record_agent_step("agent_001", steps_delta=1)["counters"]["steps"] == 1
    assert tools.search_publish_observation("agent_001", "found one issue")["observation_id"] == "obs_000001"
    assert tools.search_list_observations("run_1")[0]["summary"] == "found one issue"
    assert tools.search_wait_agent_events("run_1", timeout_seconds=0)["events"][0]["type"] == "agent_completed"
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
    assert tools.search_abort("run_1", "stop") == {"aborted": True}

    submit_result = tools.search_submit_candidate(
        "run_1",
        "c001",
        {"candidate_id": "c001", "status": "patch_ready"},
    )
    artifact = runtime.submit_candidate.call_args.kwargs["artifact"]
    assert submit_result == {"accepted": True}
    assert isinstance(artifact, ArtifactBundle)
    proposal_arg = runtime.start_batch.call_args.args[2][0]
    assert isinstance(proposal_arg, CandidateProposal)
    runtime.abort.assert_called_once_with("run_1", "stop")
    runtime.list_history.assert_called_once_with("run_1", top_n=3, sort_by="created")
    runtime.plan_next.assert_called_once_with("run_1", requested_k=1)
    runtime.start_agent_session.assert_called_once_with(
        run_id="run_1",
        candidate_id="c001",
        directive={"goal": "try one"},
        budget={"max_wall_seconds": 120},
        visibility_mode="observations",
    )
