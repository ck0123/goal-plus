from __future__ import annotations

import pytest

from agentic_any_search_mcp.goal_plus import FileGoalPlusRuntime
from agentic_any_search_mcp.models import GoalPlusSpecDraft, GoalPlusTriage


def test_goal_plus_runtime_defaults_to_gp_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = FileGoalPlusRuntime()

    assert runtime.root_dir == tmp_path / ".gp"
    assert runtime.goals_dir == tmp_path / ".gp" / "goal-plus"


def test_create_goal_plus_record_writes_state_and_event(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")

    record = runtime.create_goal(
        raw_goal="Improve the README examples",
        source_path=".",
    )

    assert record.goal_plus_id == "gp_0001"
    assert record.raw_goal == "Improve the README examples"
    assert record.status == "active"
    assert record.phase == "intake"
    assert not hasattr(record, "mode_hint")
    assert record.next_action.kind == "record_triage"  # type: ignore[union-attr]

    loaded = runtime.status(record.goal_plus_id)
    assert loaded.goal_plus_id == record.goal_plus_id
    assert runtime.list_events(record.goal_plus_id)[0]["event_type"] == "created"


def test_goal_like_triage_allows_stop_without_search(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Tidy docs wording")

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


def test_initial_search_ready_spec_requires_user_confirmation_before_freeze(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize kernel latency")
    runtime.record_triage(
        record.goal_plus_id,
        GoalPlusTriage(
            is_optimization=True,
            confidence="high",
            recommended_phase="search",
            identified_at="initial",
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
            origin="initial",
        ),
    )
    assert draft.next_action.kind == "confirm_frozen_verifier"  # type: ignore[union-attr]

    blocked = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "search_freeze_spec"},
    )
    assert blocked.decision == "block"
    assert "user confirmation" in blocked.reason

    confirmed = runtime.confirm_frozen_verifier(
        record.goal_plus_id,
        confirmed_by="user",
        evidence={"message": "freeze this verifier"},
    )
    assert confirmed.spec_draft.user_confirmed_frozen_verifier is True  # type: ignore[union-attr]
    assert confirmed.next_action.kind == "freeze_search_spec"  # type: ignore[union-attr]

    allowed = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "search_freeze_spec"},
    )
    assert allowed.decision == "allow"


def test_in_progress_search_discovery_does_not_require_user_confirmation(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Improve docs, then optimize verifier if found")
    runtime.record_triage(
        record.goal_plus_id,
        GoalPlusTriage(
            is_optimization=True,
            confidence="high",
            recommended_phase="search",
            identified_at="in_progress",
            reasons=["constructed verifier during goal execution"],
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
            origin="in_progress",
        ),
    )
    assert draft.next_action.kind == "freeze_search_spec"  # type: ignore[union-attr]
    assert draft.spec_draft.user_confirmed_frozen_verifier is False  # type: ignore[union-attr]

    gate = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "search_freeze_spec"},
    )
    assert gate.decision == "allow"


def test_high_confidence_spec_draft_links_search_and_final_audit(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize kernel latency")
    runtime.record_triage(
        record.goal_plus_id,
        GoalPlusTriage(
            is_optimization=True,
            confidence="high",
            recommended_phase="search",
            identified_at="in_progress",
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
            origin="in_progress",
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


def test_link_search_run_is_idempotent_but_does_not_overwrite(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize model")

    linked = runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    assert linked.linked_search is not None
    assert linked.linked_search.run_id == "run_001"

    linked_again = runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    assert linked_again.linked_search is not None
    assert linked_again.linked_search.run_id == "run_001"

    with pytest.raises(RuntimeError, match="already linked to search run run_001"):
        runtime.link_search_run(record.goal_plus_id, "spec_def", "run_002")

    final = runtime.status(record.goal_plus_id)
    assert final.linked_search is not None
    assert final.linked_search.frozen_spec_id == "spec_abc"
    assert final.linked_search.run_id == "run_001"


def test_record_search_result_prefers_existing_runtime_artifact_paths(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize model")
    runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    run_dir = tmp_path / ".search" / "runs" / "run_001"
    report_path = run_dir / "report.md"
    promotion_path = run_dir / "promotion" / "c001.patch"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# report\n", encoding="utf-8")
    promotion_path.parent.mkdir(parents=True)
    promotion_path.write_text("patch\n", encoding="utf-8")

    final = runtime.record_search_result(
        record.goal_plus_id,
        run_id="run_001",
        selected_candidate_id="c001",
        report_path="/tmp/model-filled-report.md",
        promotion_artifact_path="/tmp/model-filled-c001.patch",
        summary="c001 won",
    )

    assert final.linked_search is not None
    assert final.linked_search.report_path == str(report_path.resolve())
    assert final.linked_search.promotion_artifact_path == str(promotion_path.resolve())


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


def test_pre_tool_use_blocks_mutation_before_triage(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Tidy docs wording")

    gate = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "bash"},
    )

    assert gate.decision == "block"
    assert "before mutating tools" in gate.reason
    assert "Classify whether the raw goal" in gate.reason


def test_goal_mode_allows_mutation_after_triage(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Tidy docs wording")
    runtime.record_triage(
        record.goal_plus_id,
        GoalPlusTriage(
            is_optimization=False,
            confidence="high",
            recommended_phase="goal",
            reasons=["qualitative documentation task"],
        ),
    )

    gate = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "edit"},
    )

    assert gate.decision == "allow"


def test_pre_tool_use_blocks_pi_worker_launch_before_search_ready(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize kernel latency")
    runtime.record_triage(
        record.goal_plus_id,
        GoalPlusTriage(
            is_optimization=True,
            confidence="high",
            recommended_phase="search",
            identified_at="initial",
            reasons=["latency benchmark exists"],
        ),
    )

    gate = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "pi_rpc_run_worker"},
    )

    assert gate.decision == "block"
    assert "user confirmation" in gate.reason or "frozen spec draft" in gate.reason


def test_pre_tool_use_blocks_pi_candidate_driver_before_search_ready(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize kernel latency")
    runtime.record_triage(
        record.goal_plus_id,
        GoalPlusTriage(
            is_optimization=True,
            confidence="high",
            recommended_phase="search",
            identified_at="initial",
            reasons=["latency benchmark exists"],
        ),
    )

    gate = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "pi_search_run_candidate"},
    )

    assert gate.decision == "block"
    assert "user confirmation" in gate.reason or "frozen spec draft" in gate.reason


def test_pre_tool_use_accepts_camel_case_tool_name(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Maybe optimize something")

    gate = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"toolName": "search_freeze_spec"},
    )

    assert gate.decision == "block"
    assert "frozen spec draft" in gate.reason
