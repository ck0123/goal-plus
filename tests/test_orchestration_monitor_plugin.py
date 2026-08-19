from __future__ import annotations

from pathlib import Path

import pytest

from goal_plus.feature_plugins import orchestration_metadata
from goal_plus.goal_plus import FileGoalPlusRuntime
from goal_plus.monitor import goal_plus_monitor_snapshot


def _snapshot(
    root: Path,
    *,
    host_id: str,
    dispatch_operation: str,
    result_operation: str,
    dispatch_metadata: dict | None = None,
) -> dict:
    runtime = FileGoalPlusRuntime(root)
    goal = runtime.create_goal(
        "Add parser edge-case tests",
        policy={
            "execution": {
                "host": {
                    "host_id": host_id,
                    "native_reasoning_effort": "max" if host_id == "codex" else "xhigh",
                    "enforcement": "host",
                    "operations": ["spawn", "send", "wait", "interrupt", "observe"],
                }
            }
        },
    )
    runtime.record_triage(
        goal.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    runtime.upsert_work_items(
        goal.goal_plus_id,
        [
            {
                "work_item_id": "edge_tests",
                "title": "Cover parser boundaries",
                "objective": "Add signed and fractional duration tests.",
                "route": "subagent",
                "scope": ["tests/test_duration.py"],
                "acceptance": ["Focused tests pass"],
            }
        ],
    )
    agent_id = f"{host_id}-worker-1"
    dispatched = runtime.record_work_event(
        goal.goal_plus_id,
        "edge_tests",
        "dispatch",
        "Main dispatched the focused test task.",
        host=host_id,
        task_name="edge_test_worker",
        metadata=dispatch_metadata or orchestration_metadata(dispatch_operation),
    )
    item = dispatched.work_items[0]
    runtime.record_work_event(
        goal.goal_plus_id,
        "edge_tests",
        "bind",
        "Host bound the launched worker.",
        host=host_id,
        agent_id=agent_id,
        attempt_id=item.attempt_id,
        generation=item.generation,
        metadata=orchestration_metadata(dispatch_operation),
    )
    runtime.record_work_event(
        goal.goal_plus_id,
        "edge_tests",
        "result",
        "Worker returned three passing tests.",
        host=host_id,
        agent_id=agent_id,
        attempt_id=item.attempt_id,
        generation=item.generation,
        transcript_path=str(root / "private-session.jsonl"),
        metadata=orchestration_metadata(result_operation),
        evidence=[{"kind": "pytest", "outcome": "3 passed"}],
    )
    runtime.record_work_event(
        goal.goal_plus_id,
        "edge_tests",
        "accepted",
        "Main reproduced and accepted the result.",
    )
    return goal_plus_monitor_snapshot(root, goal_plus_id=goal.goal_plus_id)


def _normalized(payload: dict) -> list[tuple[str, str, str]]:
    return [
        (event["semantic_event"], event["direction"], event["work_event"])
        for event in payload["interactions"]
    ]


def test_orchestration_plugin_normalizes_codex_and_pi(tmp_path: Path) -> None:
    codex = _snapshot(
        tmp_path / "codex" / ".gp",
        host_id="codex",
        dispatch_operation="spawn_agent",
        result_operation="wait_agent",
    )["feature_plugins"]["orchestration"]
    pi = _snapshot(
        tmp_path / "pi" / ".gp",
        host_id="pi",
        dispatch_operation="pi_goal_plus_run_work_item",
        result_operation="goal-plus-pi-worker",
    )["feature_plugins"]["orchestration"]

    [codex_item] = codex["work_items"]
    [pi_item] = pi["work_items"]
    assert codex_item["task_packet"] == pi_item["task_packet"]
    assert _normalized(codex) == _normalized(pi) == [
        ("assignment", "main_to_subagent", "dispatch"),
        ("worker_update", "subagent_to_main", "bind"),
        ("result", "subagent_to_main", "result"),
        ("decision", "main_to_subagent", "accepted"),
    ]
    assert codex["summary"]["native_operations"] == ["spawn_agent", "wait_agent"]
    assert pi["summary"]["native_operations"] == [
        "pi_goal_plus_run_work_item",
        "goal-plus-pi-worker",
    ]
    assert codex_item["worker"]["transcript_available"] is True
    assert codex_item["attempt"]["generation"] == 1
    assert codex_item["attempt"]["launch_state"] == "bound"
    assert codex_item["attempt"]["stale_results"] == 0
    assert "transcript_path" not in str(codex)
    assert "private-session" not in str(codex)


def test_orchestration_plugin_shows_launching_and_stale_generation(tmp_path: Path) -> None:
    root = tmp_path / ".gp"
    runtime = FileGoalPlusRuntime(root)
    goal = runtime.create_goal("Run a fenced worker")
    runtime.record_triage(
        goal.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    runtime.upsert_work_items(
        goal.goal_plus_id,
        [
            {
                "work_item_id": "worker",
                "title": "Worker",
                "objective": "Run the bounded task",
                "route": "subagent",
            }
        ],
    )
    first = runtime.record_work_event(
        goal.goal_plus_id,
        "worker",
        "dispatch",
        "Claimed generation one.",
        host="codex",
    ).work_items[0]
    launching = goal_plus_monitor_snapshot(root, goal_plus_id=goal.goal_plus_id)[
        "feature_plugins"
    ]["orchestration"]
    assert launching["work_items"][0]["attempt"]["launch_state"] == "launching"

    runtime.record_work_event(
        goal.goal_plus_id,
        "worker",
        "failed",
        "Host launch failed.",
        attempt_id=first.attempt_id,
        generation=first.generation,
    )
    runtime.record_work_event(
        goal.goal_plus_id,
        "worker",
        "rework",
        "Retry the failed launch.",
    )
    second = runtime.record_work_event(
        goal.goal_plus_id,
        "worker",
        "dispatch",
        "Claimed generation two.",
        host="codex",
    ).work_items[0]
    runtime.record_work_event(
        goal.goal_plus_id,
        "worker",
        "bind",
        "Bound generation two.",
        agent_id="worker-new",
        attempt_id=second.attempt_id,
        generation=second.generation,
    )
    runtime.record_work_event(
        goal.goal_plus_id,
        "worker",
        "result",
        "Generation one returned late.",
        agent_id="worker-old",
        attempt_id=first.attempt_id,
        generation=first.generation,
    )

    payload = goal_plus_monitor_snapshot(root, goal_plus_id=goal.goal_plus_id)[
        "feature_plugins"
    ]["orchestration"]
    [item] = payload["work_items"]
    assert item["attempt"]["generation"] == 2
    assert item["attempt"]["launch_state"] == "bound"
    assert item["attempt"]["stale_results"] == 1
    assert payload["summary"]["stale_results_total"] == 1
    stale = next(event for event in payload["interactions"] if event["work_event"] == "stale_result")
    assert stale["generation"] == 1
    assert stale["current_generation"] == 2


def test_orchestration_plugin_is_optional_and_rejects_unknown_names(tmp_path: Path) -> None:
    snapshot = _snapshot(
        tmp_path / ".gp",
        host_id="codex",
        dispatch_operation="spawn_agent",
        result_operation="wait_agent",
    )
    goal_id = snapshot["goal_plus"]["goal_plus_id"]

    disabled = goal_plus_monitor_snapshot(
        tmp_path / ".gp",
        goal_plus_id=goal_id,
        feature_plugins=[],
    )
    assert disabled["feature_plugins"] == {}
    with pytest.raises(ValueError, match="unknown Goal Plus monitor feature"):
        goal_plus_monitor_snapshot(
            tmp_path / ".gp",
            goal_plus_id=goal_id,
            feature_plugins=["missing"],
        )
    with pytest.raises(ValueError, match="unknown Goal Plus monitor feature"):
        goal_plus_monitor_snapshot(
            tmp_path / "empty",
            feature_plugins=["missing"],
        )


def test_orchestration_metadata_rejects_prompt_content() -> None:
    with pytest.raises(ValueError, match="host tool or command name"):
        orchestration_metadata("spawn this full prompt verbatim")


def test_orchestration_plugin_drops_prompt_like_operation_metadata(tmp_path: Path) -> None:
    payload = _snapshot(
        tmp_path / ".gp",
        host_id="codex",
        dispatch_operation="unused",
        result_operation="wait_agent",
        dispatch_metadata={
            "orchestration_monitor": {
                "native_operation": "spawn this full prompt verbatim"
            }
        },
    )["feature_plugins"]["orchestration"]

    assert payload["interactions"][0]["native_operation"] is None
    assert "spawn this full prompt verbatim" not in str(payload)
