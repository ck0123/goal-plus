from __future__ import annotations

from agentic_any_search_mcp.goal_plus import FileGoalPlusRuntime
from agentic_any_search_mcp.models import GoalPlusSpecDraft, GoalPlusTriage


def test_create_goal_plus_record_writes_state_and_event(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")

    record = runtime.create_goal(
        raw_goal="Improve the README examples",
        source_path=".",
        mode_hint="auto",
    )

    assert record.goal_plus_id == "gp_0001"
    assert record.raw_goal == "Improve the README examples"
    assert record.status == "active"
    assert record.phase == "intake"
    assert record.next_action.kind == "record_triage"  # type: ignore[union-attr]

    loaded = runtime.status(record.goal_plus_id)
    assert loaded.goal_plus_id == record.goal_plus_id
    assert runtime.list_events(record.goal_plus_id)[0]["event_type"] == "created"


def test_goal_like_triage_allows_stop_without_search(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Tidy docs wording", mode_hint="auto")

    updated = runtime.record_triage(
        record.goal_plus_id,
        GoalPlusTriage(
            is_optimization=False,
            confidence="high",
            recommended_phase="goal",
            reasons=["qualitative documentation task"],
        ),
    )

    assert updated.phase == "goal"
    assert updated.next_action.kind == "work_goal_like"  # type: ignore[union-attr]
    assert updated.next_action.required is False  # type: ignore[union-attr]

    gate = runtime.gate(updated.goal_plus_id, event="stop", context={})
    assert gate.decision == "allow"
    assert gate.continuation_prompt is None


def test_spec_discovery_stop_gate_blocks_with_next_action(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize model throughput")

    updated = runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": True,
            "confidence": "medium",
            "recommended_phase": "spec_discovery",
            "scenario": "model-infer",
            "reasons": ["throughput is measurable"],
            "missing": ["baseline command", "correctness gate"],
        },
    )

    gate = runtime.gate(updated.goal_plus_id, event="stop", context={})

    assert gate.decision == "block"
    assert gate.phase == "spec_discovery"
    assert "Goal Plus is still active" in gate.continuation_prompt
    assert "baseline command" in gate.continuation_prompt


def test_high_confidence_spec_draft_links_search_and_final_audit(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize kernel latency", mode_hint="search")
    runtime.record_triage(
        record.goal_plus_id,
        GoalPlusTriage(
            is_optimization=True,
            confidence="high",
            recommended_phase="search",
            scenario="kernel-optimize",
            reasons=["latency benchmark exists"],
        ),
    )

    draft = runtime.save_spec_draft(
        record.goal_plus_id,
        GoalPlusSpecDraft(
            baseline={"command": "python bench.py"},
            metric={"name": "avg_latency_ms", "direction": "minimize"},
            correctness_gate={"command": "python verify.py"},
            edit_surface={"allow": ["kernel.py"], "deny": ["verify.py"]},
            verifier_artifacts=["verify.py", "bench.py"],
            search_spec={
                "objective": "minimize latency",
                "metric_name": "avg_latency_ms",
                "metric_direction": "minimize",
            },
            promotion_rule="correctness pass and lower latency",
            confidence="high",
        ),
    )
    assert draft.next_action.kind == "freeze_search_spec"  # type: ignore[union-attr]

    linked = runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    assert linked.phase == "search"
    assert linked.linked_search.run_id == "run_001"  # type: ignore[union-attr]

    final = runtime.record_search_result(
        record.goal_plus_id,
        run_id="run_001",
        selected_candidate_id="c001",
        report_path="/tmp/report.md",
        promotion_artifact_path="/tmp/c001.patch",
        summary="c001 won",
    )
    assert final.phase == "final_audit"

    gate = runtime.gate(record.goal_plus_id, event="stop", context={})
    assert gate.decision == "block"
    assert "audit the original raw goal" in gate.continuation_prompt

    completed = runtime.set_status(
        record.goal_plus_id,
        status="complete",
        reason="raw goal audited",
        evidence=[{"kind": "report", "path": "/tmp/report.md"}],
    )
    assert completed.status == "complete"
    assert runtime.gate(record.goal_plus_id, event="stop", context={}).decision == "allow"


def test_pre_tool_use_blocks_search_before_high_confidence_spec(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Maybe optimize something")

    gate = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "search_freeze_spec"},
    )

    assert gate.decision == "block"
    assert "frozen spec draft" in gate.reason
