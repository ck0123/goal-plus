from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

import pytest
from pydantic import ValidationError

from goal_plus import goal_plus as goal_plus_module
from goal_plus.goal_plus import FileGoalPlusRuntime
from goal_plus.models import GoalPlusExecutionPolicy


DEEPSEEK_HARNESS = {
    "host_id": "deepseek-harness",
    "native_reasoning_effort": "highest",
    "enforcement": "harness",
    "operations": ["spawn", "wait", "observe"],
}
ROOT = Path(__file__).resolve().parents[1]


def _triaged_runtime(tmp_path):
    runtime = FileGoalPlusRuntime(tmp_path / ".gp")
    record = runtime.create_goal(
        "Implement and verify a complex change",
        policy={"execution": {"host": DEEPSEEK_HARNESS}},
    )
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    return runtime, record.goal_plus_id


def _dispatch_and_bind(
    runtime: FileGoalPlusRuntime,
    goal_id: str,
    work_item_id: str,
    *,
    agent_id: str,
) -> tuple[str, int]:
    dispatched = runtime.record_work_event(
        goal_id,
        work_item_id,
        "dispatch",
        "Harness claimed the launch attempt.",
        host="deepseek-harness",
    )
    item = next(
        item for item in dispatched.work_items if item.work_item_id == work_item_id
    )
    assert item.status == "launching"
    assert item.attempt_id is not None
    runtime.record_work_event(
        goal_id,
        work_item_id,
        "bind",
        "Harness bound the launched worker.",
        host="deepseek-harness",
        agent_id=agent_id,
        attempt_id=item.attempt_id,
        generation=item.generation,
    )
    return item.attempt_id, item.generation


def test_ultra_policy_is_default_and_host_neutral(tmp_path) -> None:
    runtime = FileGoalPlusRuntime(tmp_path / ".gp")
    default = runtime.create_goal("Do the work")
    assert default.policy["execution"] == {
        "mode": "orchestrated",
        "main_reasoning_effort": "max",
        "delegation": "proactive",
        "search_routing": "auto",
        "completion": "until_terminal",
    }

    bound = runtime.create_goal(
        "Run through a DeepSeek harness",
        policy={"execution": {"host": DEEPSEEK_HARNESS}},
    )
    assert bound.policy["execution"]["host"]["host_id"] == "deepseek-harness"
    assert bound.policy["execution"]["host"]["enforcement"] == "harness"


def test_ultra_host_requires_portable_minimum_lifecycle() -> None:
    with pytest.raises(ValidationError, match="spawn, wait, and observe"):
        GoalPlusExecutionPolicy.model_validate(
            {
                "host": {
                    "host_id": "incomplete-harness",
                    "native_reasoning_effort": "max",
                    "enforcement": "harness",
                    "operations": ["spawn", "send", "wait"],
                }
            }
        )


def test_ultra_work_dag_enforces_dependencies_host_and_acceptance(tmp_path) -> None:
    runtime, goal_id = _triaged_runtime(tmp_path)
    with pytest.raises(RuntimeError, match="requires a work item plan"):
        runtime.set_status(goal_id, "complete")
    planned = runtime.upsert_work_items(
        goal_id,
        [
            {
                "work_item_id": "research",
                "title": "Research",
                "objective": "Inspect the relevant subsystem",
                "route": "subagent",
                "scope": ["src/"],
                "acceptance": ["Return concrete findings"],
            },
            {
                "work_item_id": "integrate",
                "title": "Integrate",
                "objective": "Implement and verify the final change",
                "route": "main",
                "depends_on": ["research"],
            },
        ],
    )
    assert planned.next_action.kind == "drive_orchestrated_execution"  # type: ignore[union-attr]

    with pytest.raises(RuntimeError, match="dependencies are accepted"):
        runtime.record_work_event(
            goal_id,
            "integrate",
            "dispatch",
            "Main started integration too early.",
        )
    with pytest.raises(RuntimeError, match="does not match bound Ultra host"):
        runtime.record_work_event(
            goal_id,
            "research",
            "dispatch",
            "Wrong host attempted dispatch.",
            host="codex",
        )

    dispatched = runtime.record_work_event(
        goal_id,
        "research",
        "dispatch",
        "Harness claimed the research launch.",
        host="deepseek-harness",
    )
    research = next(
        item for item in dispatched.work_items if item.work_item_id == "research"
    )
    runtime.record_work_event(
        goal_id,
        "research",
        "bind",
        "Harness bound the research worker.",
        host="deepseek-harness",
        agent_id="worker-1",
        attempt_id=research.attempt_id,
        generation=research.generation,
    )
    with pytest.raises(RuntimeError, match="after it has started"):
        runtime.upsert_work_items(
            goal_id,
            [
                {
                    "work_item_id": "research",
                    "title": "Research a different scope",
                    "objective": "Change the assignment after launch",
                    "route": "subagent",
                }
            ],
        )
    runtime.record_work_event(
        goal_id,
        "research",
        "result",
        "Found the relevant ownership boundary.",
        host="deepseek-harness",
        attempt_id=research.attempt_id,
        generation=research.generation,
        evidence=[{"kind": "source", "path": "src/module.py"}],
    )
    runtime.record_work_event(
        goal_id,
        "research",
        "accepted",
        "Main verified and accepted the findings.",
    )
    runtime.record_work_event(goal_id, "integrate", "dispatch", "Main started integration.")
    runtime.record_work_event(goal_id, "integrate", "result", "Implementation and tests are ready.")
    completed_work = runtime.record_work_event(
        goal_id,
        "integrate",
        "accepted",
        "Main verified the implementation and tests.",
        evidence=[{"kind": "pytest", "result": "passed"}],
    )

    assert completed_work.phase == "final_audit"
    assert completed_work.next_action.kind == "audit_raw_goal"  # type: ignore[union-attr]
    assert runtime.set_status(goal_id, "complete").status == "complete"


def test_ultra_completion_rejects_unaccepted_work_and_supports_rework(tmp_path) -> None:
    runtime, goal_id = _triaged_runtime(tmp_path)
    runtime.upsert_work_items(
        goal_id,
        [
            {
                "work_item_id": "implementation",
                "title": "Implementation",
                "objective": "Implement the change",
                "route": "subagent",
            }
        ],
    )
    attempt_id, generation = _dispatch_and_bind(
        runtime,
        goal_id,
        "implementation",
        agent_id="worker-implementation",
    )
    runtime.record_work_event(
        goal_id,
        "implementation",
        "result",
        "Initial implementation returned.",
        attempt_id=attempt_id,
        generation=generation,
    )
    with pytest.raises(RuntimeError, match="implementation=result_ready"):
        runtime.set_status(goal_id, "complete")

    reworked = runtime.record_work_event(
        goal_id,
        "implementation",
        "rework",
        "Main found a missing edge case.",
    )
    assert reworked.work_items[0].result_digest is not None
    resumed = runtime.record_work_event(
        goal_id,
        "implementation",
        "message",
        "Host resumed the bound worker.",
        attempt_id=attempt_id,
        generation=generation,
    )
    assert resumed.work_items[0].result_digest is None
    runtime.record_work_event(
        goal_id,
        "implementation",
        "result",
        "Edge case fixed and retested.",
        attempt_id=attempt_id,
        generation=generation,
    )
    accepted = runtime.record_work_event(
        goal_id,
        "implementation",
        "accepted",
        "Main accepted the corrected result.",
    )
    assert accepted.work_items[0].status == "accepted"


def test_ultra_attempt_reconciliation_fences_stale_and_duplicate_results(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, goal_id = _triaged_runtime(tmp_path)
    runtime.upsert_work_items(
        goal_id,
        [
            {
                "work_item_id": "implementation",
                "title": "Implementation",
                "objective": "Implement the change",
                "route": "subagent",
            }
        ],
    )
    now = 1_000.0
    monkeypatch.setattr(goal_plus_module.time, "time", lambda: now)
    first = runtime.record_work_event(
        goal_id,
        "implementation",
        "dispatch",
        "Claimed the first launch attempt.",
        host="deepseek-harness",
        launch_ttl_seconds=30,
    ).work_items[0]

    with pytest.raises(RuntimeError, match="still launching"):
        runtime.record_work_event(
            goal_id,
            "implementation",
            "dispatch",
            "Tried to reclaim a live launch.",
            host="deepseek-harness",
            launch_ttl_seconds=30,
        )
    with pytest.raises(RuntimeError, match="cannot record result while.*launching"):
        runtime.record_work_event(
            goal_id,
            "implementation",
            "result",
            "Returned before host binding.",
            attempt_id=first.attempt_id,
            generation=first.generation,
        )

    now += 31
    second = runtime.record_work_event(
        goal_id,
        "implementation",
        "dispatch",
        "Reconciled the expired launch attempt.",
        host="deepseek-harness",
        launch_ttl_seconds=30,
    ).work_items[0]
    assert second.generation == first.generation + 1
    assert second.attempt_id != first.attempt_id
    bound = runtime.record_work_event(
        goal_id,
        "implementation",
        "bind",
        "Bound the replacement worker.",
        host="deepseek-harness",
        agent_id="worker-new",
        attempt_id=second.attempt_id,
        generation=second.generation,
    ).work_items[0]
    assert bound.status == "active"
    assert bound.agent_id == "worker-new"

    stale = runtime.record_work_event(
        goal_id,
        "implementation",
        "result",
        "Old worker returned late.",
        host="deepseek-harness",
        agent_id="worker-old",
        attempt_id=first.attempt_id,
        generation=first.generation,
    ).work_items[0]
    assert stale.status == "active"
    assert stale.agent_id == "worker-new"
    stale_event = runtime.list_events(goal_id)[-1]["payload"]
    assert stale_event["event"] == "stale_result"
    assert stale_event["generation"] == first.generation
    assert stale_event["current_generation"] == second.generation

    result_kwargs = {
        "host": "deepseek-harness",
        "agent_id": "worker-new",
        "attempt_id": second.attempt_id,
        "generation": second.generation,
        "evidence": [{"kind": "pytest", "outcome": "passed"}],
    }
    ready = runtime.record_work_event(
        goal_id,
        "implementation",
        "result",
        "Replacement worker completed.",
        **result_kwargs,
    )
    event_count = len(runtime.list_events(goal_id))
    duplicate = runtime.record_work_event(
        goal_id,
        "implementation",
        "result",
        "Replacement worker completed.",
        **result_kwargs,
    )
    assert duplicate == ready
    assert len(runtime.list_events(goal_id)) == event_count
    with pytest.raises(RuntimeError, match="conflicting result"):
        runtime.record_work_event(
            goal_id,
            "implementation",
            "result",
            "Conflicting replacement result.",
            **result_kwargs,
        )


def test_ultra_search_route_and_goal_revision_are_auditable(tmp_path) -> None:
    runtime, goal_id = _triaged_runtime(tmp_path)
    runtime.upsert_work_items(
        goal_id,
        [
            {
                "work_item_id": "optimize",
                "title": "Optimize",
                "objective": "Explore independently verifiable alternatives",
                "route": "search",
            }
        ],
    )
    with pytest.raises(ValueError, match="requires search_run_id"):
        runtime.record_work_event(
            goal_id,
            "optimize",
            "search_routed",
            "Search route selected.",
        )
    runtime.record_work_event(
        goal_id,
        "optimize",
        "search_routed",
        "Search route selected after spec validation.",
        search_run_id="run_001",
    )
    revised = runtime.update_goal(
        goal_id,
        "Implement a revised complex change",
        expected_revision=1,
    )
    assert revised.work_items[0].status == "superseded"


def test_ultra_concurrent_work_events_are_serialized(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, goal_id = _triaged_runtime(tmp_path)
    runtime.upsert_work_items(
        goal_id,
        [
            {
                "work_item_id": work_item_id,
                "title": work_item_id,
                "objective": f"Run {work_item_id}",
                "route": "subagent",
            }
            for work_item_id in ("worker-a", "worker-b")
        ],
    )

    original_load = FileGoalPlusRuntime._load_record
    counter_lock = threading.Lock()
    active_loads = 0
    max_active_loads = 0

    def slow_load(self, requested_goal_id):
        nonlocal active_loads, max_active_loads
        with counter_lock:
            active_loads += 1
            max_active_loads = max(max_active_loads, active_loads)
        try:
            time.sleep(0.05)
            return original_load(self, requested_goal_id)
        finally:
            with counter_lock:
                active_loads -= 1

    monkeypatch.setattr(FileGoalPlusRuntime, "_load_record", slow_load)
    start = threading.Barrier(2)

    def dispatch(work_item_id: str) -> None:
        start.wait(timeout=5)
        FileGoalPlusRuntime(runtime.root_dir).record_work_event(
            goal_id,
            work_item_id,
            "dispatch",
            f"Dispatched {work_item_id}",
            host="deepseek-harness",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(dispatch, ("worker-a", "worker-b")))

    record = runtime.status(goal_id)
    assert max_active_loads == 1
    assert {item.work_item_id: item.status for item in record.work_items} == {
        "worker-a": "launching",
        "worker-b": "launching",
    }


def test_ultra_assets_keep_core_host_neutral_and_wire_codex_and_pi() -> None:
    models = (ROOT / "src" / "goal_plus" / "models.py").read_text(encoding="utf-8")
    codex = (ROOT / ".codex" / "skills" / "goal-plus" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    pi = (ROOT / ".pi" / "extensions" / "goal-plus.ts").read_text(encoding="utf-8")
    pi_worker = (ROOT / "src" / "goal_plus" / "pi_worker.py").read_text(
        encoding="utf-8"
    )
    docs = (ROOT / "docs" / "ultra.md").read_text(encoding="utf-8")

    work_item = models.split("class GoalPlusWorkItem(", 1)[1].split(
        "class GoalPlusActiveSession", 1
    )[0]
    assert "AgentHostKind" not in work_item
    assert "host: str | None" in work_item
    assert "attempt_id: str | None" in work_item
    assert "generation: int" in work_item
    assert "goal-plus-ultra-v1" in models
    assert "spawn_agent" in codex
    assert 'fork_turns="none"' in codex
    assert "`bind`" in codex
    assert 'pi.setThinkingLevel("xhigh")' in pi
    assert 'thinking_level: "xhigh"' in pi
    assert 'name: "pi_goal_plus_run_work_item"' in pi
    assert 'native_operation: "pi_goal_plus_run_work_item"' in pi
    assert 'native_operation: "goal-plus-pi-worker"' in pi
    assert 'executionMode: "concurrent"' in pi
    assert 'role: "ordinary"' in pi
    assert "goal_plus_work_item" in pi
    assert "sessionId = String(workItem.agent_id)" in pi
    assert '"bind"' in pi_worker
    assert '"native_operation":"spawn_agent"' in codex
    assert "deepseek-harness" in docs
