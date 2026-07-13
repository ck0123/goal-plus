from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from goal_plus.goal_plus import FileGoalPlusRuntime
from goal_plus.models import GoalPlusSpecDraft, GoalPlusTriage


def _write_search_run(root, run_id: str, frozen_spec_id: str) -> None:
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_id, "frozen_spec_id": frozen_spec_id}),
        encoding="utf-8",
    )


def _complete_search_spec() -> dict:
    return {
        "objective": "minimize latency",
        "metric_name": "avg_latency_ms",
        "metric_direction": "minimize",
        "source_path": ".",
        "edit_surface": {"allow": ["kernel.py"], "deny": ["verify.py"]},
        "budget": {"max_candidates": 2, "max_parallel": 1},
        "process_verifiers": [
            {
                "name": "latency",
                "role": "ranking_signal",
                "command": ["python", "verify.py"],
            }
        ],
    }


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


def test_update_goal_creates_revision_and_supersedes_pending_check(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal(
        "Implement the first requirement",
        policy={"final_check": {"mode": "required"}},
    )
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    request = runtime.prepare_final_check(record.goal_plus_id, "codex")

    updated = runtime.update_goal(
        record.goal_plus_id,
        raw_goal="Implement both the first and second requirements",
        expected_revision=1,
        reason="user added a requirement after interruption",
    )

    assert updated.goal_plus_id == record.goal_plus_id
    assert updated.goal_revision == 2
    assert [revision.revision for revision in updated.goal_revisions] == [1, 2]
    assert updated.goal_revisions[-1].raw_goal == updated.raw_goal
    assert updated.final_checks[0].check_id == request["check"]["check_id"]
    assert updated.final_checks[0].status == "superseded"
    assert updated.phase == "intake"
    assert updated.triage is None
    assert updated.next_action.kind == "record_triage"  # type: ignore[union-attr]
    with pytest.raises(RuntimeError, match="revision conflict"):
        runtime.update_goal(
            record.goal_plus_id,
            raw_goal="stale edit",
            expected_revision=1,
        )


def test_required_final_check_blocks_stop_and_completes_only_after_pass(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal(
        "Ship a complete implementation",
        source_path=".",
        policy={"final_check": {"mode": "required"}},
    )
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )

    gate = runtime.gate(record.goal_plus_id, event="stop", context={})
    assert gate.decision == "block"
    assert "independent final check" in gate.continuation_prompt
    with pytest.raises(RuntimeError, match="requires a passing final check"):
        runtime.set_status(record.goal_plus_id, status="complete")

    first = runtime.prepare_final_check(record.goal_plus_id, "codex")
    resumed = runtime.prepare_final_check(record.goal_plus_id, "codex")
    assert resumed["check"]["check_id"] == first["check"]["check_id"]
    assert first["launch"]["tool"] == "spawn_agent"
    assert first["launch"]["fork_turns"] == "none"
    assert first["launch"]["agent_type"] == "goal_plus_final_checker"
    assert "goal_plus_submit_final_check" in first["launch"]["message"]

    failed = runtime.submit_final_check(
        record.goal_plus_id,
        check_id=first["check"]["check_id"],
        goal_revision=1,
        verdict="fail",
        summary="One explicit requirement is still missing.",
        findings=[{"requirement": "tests", "problem": "missing interruption coverage"}],
        evidence=[{"kind": "test_inventory", "result": "missing"}],
    )
    assert failed.status == "active"
    assert failed.next_action.kind == "address_final_check_findings"  # type: ignore[union-attr]
    assert runtime.gate(
        record.goal_plus_id,
        event="subagent_stop",
        context={"agent_type": "goal_plus_final_checker"},
    ).decision == "allow"

    second = runtime.prepare_final_check(record.goal_plus_id, "codex")
    assert second["check"]["check_id"] != first["check"]["check_id"]
    completed = runtime.submit_final_check(
        record.goal_plus_id,
        check_id=second["check"]["check_id"],
        goal_revision=1,
        verdict="pass",
        summary="Every requirement is implemented and covered.",
        evidence=[{"kind": "pytest", "result": "passed"}],
    )
    assert completed.status == "complete"
    assert completed.final_checks[-1].status == "passed"
    assert runtime.gate(record.goal_plus_id, event="stop", context={}).decision == "allow"


def test_interrupted_final_check_requires_fresh_host_review(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal(
        "Ship after an independent review",
        source_path=".",
        policy={"final_check": {"mode": "required"}},
    )
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    first = runtime.prepare_final_check(record.goal_plus_id, "pi")
    assert first["launch"]["tool"] == "pi_goal_plus_run_final_check"
    assert first["launch"]["role"] == "final-checker"

    interrupted = runtime.submit_final_check(
        record.goal_plus_id,
        check_id=first["check"]["check_id"],
        goal_revision=1,
        verdict="interrupted",
        summary="Pi reviewer timed out before recording a verdict.",
        checker_metadata={"timed_out": True},
    )
    assert interrupted.status == "active"
    assert interrupted.final_checks[-1].status == "interrupted"
    assert interrupted.next_action.kind == "retry_final_check"  # type: ignore[union-attr]
    assert runtime.gate(record.goal_plus_id, event="stop", context={}).decision == "block"

    second = runtime.prepare_final_check(record.goal_plus_id, "pi")
    assert second["check"]["check_id"] != first["check"]["check_id"]
    assert second["check"]["status"] == "pending"


def test_pending_final_checker_can_stop_without_blocking_parent_cleanup(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal(
        "Review me",
        policy={"final_check": {"mode": "required"}},
    )
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    runtime.prepare_final_check(record.goal_plus_id, "codex")

    gate = runtime.gate(
        record.goal_plus_id,
        event="subagent_stop",
        context={"agent_type": "goal_plus_final_checker"},
    )
    assert gate.decision == "allow"


def test_goal_update_keeps_old_search_result_historical(tmp_path) -> None:
    root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(root)
    record = runtime.create_goal("Optimize revision one")
    _write_search_run(root, "run_001", "spec_abc")
    linked = runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    assert linked.search_tasks[0].goal_revision == 1

    revised = runtime.update_goal(
        record.goal_plus_id,
        "Optimize a different revision two objective",
        expected_revision=1,
    )
    assert revised.linked_search is None

    run_dir = root / "runs" / "run_001"
    (run_dir / "report.md").write_text("# report\n", encoding="utf-8")
    promotion = run_dir / "promotion" / "c001.patch"
    promotion.parent.mkdir(parents=True)
    promotion.write_text("patch\n", encoding="utf-8")
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "frozen_spec_id": "spec_abc",
                "state": "promoted",
                "selected_candidate_id": "c001",
            }
        ),
        encoding="utf-8",
    )
    late = runtime.record_search_result(record.goal_plus_id, "run_001")
    assert late.goal_revision == 2
    assert late.phase == "intake"
    assert late.linked_search is None
    assert late.search_tasks[0].result_recorded_at is not None


@pytest.mark.pi
def test_pi_final_check_launch_is_foreground_stateless_and_revision_bound(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".gp")
    record = runtime.create_goal(
        "Verify the Pi delivery",
        source_path=".",
        policy={"final_check": {"mode": "required"}},
    )
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    request = runtime.prepare_final_check(record.goal_plus_id, "pi")
    launch = request["launch"]
    assert launch["tool"] == "pi_goal_plus_run_final_check"
    assert launch["role"] == "final-checker"
    assert launch["session_id"] == request["check"]["check_id"]
    assert launch["budget_control"]["max_runtime_seconds"] == 300
    assert "goal_plus_submit_final_check" in launch["prompt"]

    runtime.update_goal(
        record.goal_plus_id,
        "Verify the revised Pi delivery",
        expected_revision=1,
    )
    with pytest.raises(RuntimeError, match="stale for goal revision"):
        runtime.submit_final_check(
            record.goal_plus_id,
            check_id=request["check"]["check_id"],
            goal_revision=1,
            verdict="pass",
            summary="old review",
            evidence=[{"kind": "pytest", "result": "passed"}],
        )


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


@pytest.mark.parametrize("tool_name", ["bash", "write", "edit", "apply_patch"])
def test_spec_discovery_allows_host_tools_needed_to_discover_and_materialize_verifier(
    tmp_path, tool_name: str
) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize model throughput")
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": True,
            "confidence": "medium",
            "recommended_phase": "spec_discovery",
            "reasons": ["throughput is measurable"],
            "missing": ["public verifier"],
        },
    )

    gate = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": tool_name},
    )

    assert gate.decision == "allow"


def test_ready_spec_draft_requires_complete_search_spec_at_save_boundary(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize model throughput")
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": True,
            "confidence": "high",
            "recommended_phase": "search",
            "reasons": ["throughput is measurable"],
        },
    )

    with pytest.raises(ValidationError):
        runtime.save_spec_draft(
            record.goal_plus_id,
            GoalPlusSpecDraft(
                baseline={},
                metric={"name": "throughput"},
                correctness_gate={},
                edit_surface={"allow": ["kernel.py"]},
                search_spec={"metric_name": "throughput"},
                promotion_rule="highest valid throughput",
                confidence="high",
            ),
        )

    assert runtime.status(record.goal_plus_id).spec_draft is None


def test_initial_search_ready_spec_autonomously_allows_freeze(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize kernel latency")
    triaged = runtime.record_triage(
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
    assert triaged.next_action.kind == "draft_initial_search_spec"  # type: ignore[union-attr]
    assert "user confirmation" not in triaged.next_action.description  # type: ignore[union-attr]

    draft = runtime.save_spec_draft(
        record.goal_plus_id,
        GoalPlusSpecDraft(
            baseline={"command": "python bench.py"},
            metric={"name": "avg_latency_ms", "direction": "minimize"},
            correctness_gate={"command": "python verify.py"},
            edit_surface={"allow": ["kernel.py"], "deny": ["verify.py"]},
            verifier_artifacts=["verify.py", "bench.py"],
            search_spec=_complete_search_spec(),
            promotion_rule="correctness pass and lower latency",
            confidence="high",
            origin="initial",
        ),
    )
    assert draft.next_action.kind == "freeze_search_spec"  # type: ignore[union-attr]
    assert draft.spec_draft.user_confirmed_frozen_verifier is False  # type: ignore[union-attr]

    for tool_name in ("bash", "write", "edit"):
        discovery_tool = runtime.gate(
            record.goal_plus_id,
            event="pre_tool_use",
            context={"tool_name": tool_name},
        )
        assert discovery_tool.decision == "allow"

    stop_gate = runtime.gate(record.goal_plus_id, event="stop", context={})
    assert stop_gate.decision == "block"
    assert "Autonomously freeze" in stop_gate.reason

    allowed = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "search_freeze_spec"},
    )
    assert allowed.decision == "allow"

    # The legacy confirmation API remains available for old callers and audit
    # evidence, but Search readiness no longer depends on it.
    confirmed = runtime.confirm_frozen_verifier(
        record.goal_plus_id,
        confirmed_by="user",
        evidence={"message": "freeze this verifier"},
    )
    assert confirmed.spec_draft.user_confirmed_frozen_verifier is True  # type: ignore[union-attr]
    assert confirmed.next_action.kind == "freeze_search_spec"  # type: ignore[union-attr]

    still_allowed = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "search_freeze_spec"},
    )
    assert still_allowed.decision == "allow"


def test_in_progress_search_discovery_uses_same_autonomous_admission(tmp_path) -> None:
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
            search_spec=_complete_search_spec(),
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
            search_spec=_complete_search_spec(),
            promotion_rule="correctness pass and lower latency",
            confidence="high",
            origin="in_progress",
        ),
    )
    assert draft.next_action.kind == "freeze_search_spec"  # type: ignore[union-attr]

    _write_search_run(tmp_path / ".search", "run_001", "spec_abc")
    linked = runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    assert linked.phase == "search"
    assert linked.linked_search.run_id == "run_001"  # type: ignore[union-attr]

    run_dir = tmp_path / ".search" / "runs" / "run_001"
    report_path = run_dir / "report.md"
    promotion_path = run_dir / "promotion" / "c001.patch"
    promotion_path.parent.mkdir(parents=True)
    report_path.write_text("# report\n", encoding="utf-8")
    promotion_path.write_text("patch\n", encoding="utf-8")
    (run_dir / "run.json").write_text(
        json.dumps({"state": "promoted", "selected_candidate_id": "c001"}),
        encoding="utf-8",
    )

    final = runtime.record_search_result(
        record.goal_plus_id,
        run_id="run_001",
        selected_candidate_id="c001",
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


def test_pre_tool_use_defers_promotion_selection_check_to_search_runtime(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize kernel latency")
    runtime.record_triage(
        record.goal_plus_id,
        GoalPlusTriage(
            is_optimization=True,
            confidence="high",
            recommended_phase="search",
            identified_at="in_progress",
            reasons=["latency benchmark exists"],
        ),
    )
    runtime.save_spec_draft(
        record.goal_plus_id,
        GoalPlusSpecDraft(
            baseline={"command": "python bench.py"},
            metric={"name": "avg_latency_ms", "direction": "minimize"},
            correctness_gate={"command": "python verify.py"},
            edit_surface={"allow": ["kernel.py"], "deny": ["verify.py"]},
            verifier_artifacts=["verify.py", "bench.py"],
            search_spec=_complete_search_spec(),
            promotion_rule="correctness pass and lower latency",
            confidence="high",
            origin="in_progress",
        ),
    )
    _write_search_run(tmp_path / ".search", "run_001", "spec_abc")
    runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")

    gate = runtime.gate(
        record.goal_plus_id,
        event="pre_tool_use",
        context={"tool_name": "search_promote"},
    )

    assert gate.decision == "allow"


def test_link_search_run_is_idempotent_and_appends_distinct_search_tasks(tmp_path) -> None:
    root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(root)
    record = runtime.create_goal("Optimize model")
    _write_search_run(root, "run_001", "spec_abc")
    _write_search_run(root, "run_002", "spec_def")

    linked = runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    assert linked.linked_search is not None
    assert linked.linked_search.run_id == "run_001"

    linked_again = runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    assert linked_again.linked_search is not None
    assert linked_again.linked_search.run_id == "run_001"

    second = runtime.link_search_run(record.goal_plus_id, "spec_def", "run_002")
    assert second.linked_search is not None
    assert second.linked_search.run_id == "run_002"
    assert [task.run_id for task in second.search_tasks] == ["run_001", "run_002"]

    final = runtime.status(record.goal_plus_id)
    assert final.linked_search is not None
    assert final.linked_search.frozen_spec_id == "spec_def"
    assert final.linked_search.run_id == "run_002"
    assert [task.run_id for task in final.search_tasks] == ["run_001", "run_002"]
    assert len(
        [event for event in runtime.list_events(record.goal_plus_id) if event["event_type"] == "search_linked"]
    ) == 2


def test_goal_status_recovers_legacy_search_task_history_from_events(tmp_path) -> None:
    root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(root)
    record = runtime.create_goal("Optimize model")
    _write_search_run(root, "run_001", "spec_abc")
    _write_search_run(root, "run_002", "spec_def")
    runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    latest = runtime.link_search_run(record.goal_plus_id, "spec_def", "run_002")

    legacy_payload = latest.model_dump(mode="json")
    legacy_payload.pop("search_tasks")
    runtime._goal_path(record.goal_plus_id).write_text(  # noqa: SLF001
        json.dumps(legacy_payload),
        encoding="utf-8",
    )

    recovered = runtime.status(record.goal_plus_id)

    assert [task.run_id for task in recovered.search_tasks] == ["run_001", "run_002"]
    assert recovered.search_tasks[0].linked_at is not None
    assert recovered.linked_search is not None
    assert recovered.linked_search.run_id == "run_002"


def test_recording_superseded_search_result_preserves_current_task(tmp_path) -> None:
    root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(root)
    record = runtime.create_goal("Optimize model")
    _write_search_run(root, "run_001", "spec_abc")
    _write_search_run(root, "run_002", "spec_def")
    runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    runtime.link_search_run(record.goal_plus_id, "spec_def", "run_002")

    run_dir = root / "runs" / "run_001"
    report_path = run_dir / "report.md"
    promotion_path = run_dir / "promotion" / "c001.patch"
    promotion_path.parent.mkdir(parents=True)
    report_path.write_text("# report\n", encoding="utf-8")
    promotion_path.write_text("patch\n", encoding="utf-8")
    (run_dir / "run.json").write_text(
        json.dumps({"state": "promoted", "selected_candidate_id": "c001"}),
        encoding="utf-8",
    )

    updated = runtime.record_search_result(
        record.goal_plus_id,
        run_id="run_001",
        selected_candidate_id="c001",
        summary="older task completed",
    )

    assert updated.phase == "search"
    assert updated.linked_search is not None
    assert updated.linked_search.run_id == "run_002"
    assert updated.search_tasks[0].selected_candidate_id == "c001"
    assert updated.search_tasks[0].result_recorded_at is not None


def test_link_search_run_rejects_missing_or_mismatched_runtime_run(tmp_path) -> None:
    root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(root)
    record = runtime.create_goal("Optimize model")

    with pytest.raises(FileNotFoundError, match="Call search_create"):
        runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_0001")

    _write_search_run(root, "run_real", "spec_other")
    with pytest.raises(ValueError, match="belongs to frozen spec spec_other"):
        runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_real")

    final = runtime.status(record.goal_plus_id)
    assert final.phase == "intake"
    assert final.linked_search is None


def test_record_search_result_prefers_existing_runtime_artifact_paths(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize model")
    _write_search_run(tmp_path / ".search", "run_001", "spec_abc")
    runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    run_dir = tmp_path / ".search" / "runs" / "run_001"
    report_path = run_dir / "report.md"
    promotion_path = run_dir / "promotion" / "c001.patch"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# report\n", encoding="utf-8")
    promotion_path.parent.mkdir(parents=True)
    promotion_path.write_text("patch\n", encoding="utf-8")
    (run_dir / "run.json").write_text(
        json.dumps({"state": "promoted", "selected_candidate_id": "c001"}),
        encoding="utf-8",
    )

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


def test_record_search_result_rejects_unpromoted_run(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".search")
    record = runtime.create_goal("Optimize model")
    _write_search_run(tmp_path / ".search", "run_001", "spec_abc")
    runtime.link_search_run(record.goal_plus_id, "spec_abc", "run_001")
    run_dir = tmp_path / ".search" / "runs" / "run_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text("# report\n", encoding="utf-8")
    (run_dir / "run.json").write_text(
        json.dumps({"state": "ready_to_promote", "selected_candidate_id": "c001"}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="search_promote"):
        runtime.record_search_result(
            record.goal_plus_id,
            run_id="run_001",
            selected_candidate_id="c001",
        )

    assert runtime.status(record.goal_plus_id).phase == "search"


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


@pytest.mark.pi
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
    assert "frozen spec draft" in gate.reason


@pytest.mark.pi
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
    assert "frozen spec draft" in gate.reason


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
