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
    triaged = runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    return runtime, triaged


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
        f"Perform {work_item_id}",
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


def test_ultra_main_only_goal_needs_no_execution_plan(tmp_path) -> None:
    runtime, record = _triaged_runtime(tmp_path)

    assert record.next_action is not None
    assert record.next_action.kind == "execute_goal"
    assert record.work_items == []
    assert runtime.set_status(record.goal_plus_id, "complete").status == "complete"


def test_ultra_dispatch_lazily_creates_record_and_only_live_work_blocks_completion(
    tmp_path,
) -> None:
    runtime, record = _triaged_runtime(tmp_path)
    goal_id = record.goal_plus_id

    with pytest.raises(RuntimeError, match="does not match bound Ultra host"):
        runtime.record_work_event(
            goal_id,
            "tests",
            "dispatch",
            "Add focused tests.",
            host="codex",
        )
    assert runtime.status(goal_id).work_items == []

    dispatched = runtime.record_work_event(
        goal_id,
        "tests",
        "dispatch",
        "Add focused tests.",
        host="deepseek-harness",
    )
    [item] = dispatched.work_items
    assert item.objective == "Add focused tests."
    assert item.status == "launching"
    assert item.generation == 1
    assert item.attempt_id is not None
    with pytest.raises(RuntimeError, match="active subagent dispatches: tests=launching"):
        runtime.set_status(goal_id, "complete")

    runtime.record_work_event(
        goal_id,
        "tests",
        "bind",
        "Worker started.",
        agent_id="worker-tests",
        attempt_id=item.attempt_id,
        generation=item.generation,
    )
    with pytest.raises(RuntimeError, match="tests=active"):
        runtime.set_status(goal_id, "complete")

    runtime.record_work_event(
        goal_id,
        "tests",
        "result",
        "Focused tests pass.",
        agent_id="worker-tests",
        attempt_id=item.attempt_id,
        generation=item.generation,
        evidence=[{"kind": "pytest", "outcome": "passed"}],
    )
    assert runtime.set_status(goal_id, "complete").status == "complete"


def test_ultra_rework_reuses_worker_with_a_new_fenced_generation(tmp_path) -> None:
    runtime, record = _triaged_runtime(tmp_path)
    goal_id = record.goal_plus_id
    first_attempt, first_generation = _dispatch_and_bind(
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
        agent_id="worker-implementation",
        attempt_id=first_attempt,
        generation=first_generation,
    )

    reworked = runtime.record_work_event(
        goal_id,
        "implementation",
        "rework",
        "Fix the missing edge case.",
    )
    item = reworked.work_items[0]
    assert item.status == "active"
    assert item.agent_id == "worker-implementation"
    assert item.objective == "Fix the missing edge case."
    assert item.generation == first_generation + 1
    assert item.attempt_id != first_attempt
    assert item.result_digest is None

    runtime.record_work_event(
        goal_id,
        "implementation",
        "result",
        "Old result returned again.",
        agent_id="worker-implementation",
        attempt_id=first_attempt,
        generation=first_generation,
    )
    assert runtime.list_events(goal_id)[-1]["payload"]["event"] == "stale_result"

    ready = runtime.record_work_event(
        goal_id,
        "implementation",
        "result",
        "Edge case fixed and retested.",
        agent_id="worker-implementation",
        attempt_id=item.attempt_id,
        generation=item.generation,
    )
    assert ready.work_items[0].status == "result_ready"
    accepted = runtime.record_work_event(
        goal_id,
        "implementation",
        "accepted",
        "Main checked and used the result.",
    )
    assert accepted.work_items[0].status == "accepted"
    assert accepted.work_items[0].result_summary == "Edge case fixed and retested."


def test_ultra_launch_reconciliation_fences_stale_and_duplicate_results(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, record = _triaged_runtime(tmp_path)
    goal_id = record.goal_plus_id
    now = 1_000.0
    monkeypatch.setattr(goal_plus_module.time, "time", lambda: now)
    first = runtime.record_work_event(
        goal_id,
        "implementation",
        "dispatch",
        "Implement the change.",
        host="deepseek-harness",
        launch_ttl_seconds=30,
    ).work_items[0]

    with pytest.raises(RuntimeError, match="still launching"):
        runtime.record_work_event(
            goal_id,
            "implementation",
            "dispatch",
            "Retry too early.",
            host="deepseek-harness",
            launch_ttl_seconds=30,
        )

    now += 31
    second = runtime.record_work_event(
        goal_id,
        "implementation",
        "dispatch",
        "Retry after launch timeout.",
        host="deepseek-harness",
        launch_ttl_seconds=30,
    ).work_items[0]
    assert second.generation == first.generation + 1
    assert second.attempt_id != first.attempt_id
    runtime.record_work_event(
        goal_id,
        "implementation",
        "bind",
        "Bound replacement worker.",
        agent_id="worker-new",
        attempt_id=second.attempt_id,
        generation=second.generation,
    )

    stale = runtime.record_work_event(
        goal_id,
        "implementation",
        "result",
        "Old worker returned late.",
        agent_id="worker-old",
        attempt_id=first.attempt_id,
        generation=first.generation,
    ).work_items[0]
    assert stale.status == "active"
    assert stale.agent_id == "worker-new"
    stale_event = runtime.list_events(goal_id)[-1]["payload"]
    assert stale_event["event"] == "stale_result"
    assert stale_event["current_generation"] == second.generation

    result_kwargs = {
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


def test_ultra_goal_revision_supersedes_live_dispatch(tmp_path) -> None:
    runtime, record = _triaged_runtime(tmp_path)
    runtime.record_work_event(
        record.goal_plus_id,
        "worker",
        "dispatch",
        "Inspect the original goal.",
        host="deepseek-harness",
    )
    revised = runtime.update_goal(
        record.goal_plus_id,
        "Implement a revised complex change",
        expected_revision=1,
    )
    assert revised.work_items[0].status == "superseded"


def test_ultra_concurrent_lazy_dispatches_are_serialized(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, record = _triaged_runtime(tmp_path)
    goal_id = record.goal_plus_id
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
            f"Perform {work_item_id}",
            host="deepseek-harness",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(dispatch, ("worker-a", "worker-b")))

    current = runtime.status(goal_id)
    assert max_active_loads == 1
    assert {item.work_item_id: item.status for item in current.work_items} == {
        "worker-a": "launching",
        "worker-b": "launching",
    }


def test_ultra_assets_use_native_hosts_without_dag_planning() -> None:
    models = (ROOT / "src" / "goal_plus" / "models.py").read_text(encoding="utf-8")
    core = (ROOT / "src" / "goal_plus" / "goal_plus.py").read_text(encoding="utf-8")
    server = (ROOT / "src" / "goal_plus" / "server.py").read_text(encoding="utf-8")
    codex = (ROOT / ".codex" / "skills" / "goal-plus" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    pi = (ROOT / ".pi" / "extensions" / "goal-plus.ts").read_text(encoding="utf-8")
    pi_worker = (ROOT / "src" / "goal_plus" / "pi_worker.py").read_text(
        encoding="utf-8"
    )

    work_item = models.split("class GoalPlusWorkItem(", 1)[1].split(
        "class GoalPlusActiveSession", 1
    )[0]
    assert "depends_on" not in work_item
    assert "route:" not in work_item
    assert "attempt_id: str | None" in work_item
    assert "generation: int" in work_item
    assert "GoalPlusWorkItemInput" not in models
    assert "upsert_work_items" not in core
    assert "goal_plus_upsert_work_items" not in server
    assert "goal-plus-ultra-v1" in models
    assert "spawn_agent" in codex
    assert 'fork_turns="none"' in codex
    assert "bind" in codex
    assert 'pi.setThinkingLevel("xhigh")' in pi
    assert 'thinking_level: "xhigh"' in pi
    assert 'name: "pi_goal_plus_run_work_item"' in pi
    assert "task: Type.String" in pi
    assert 'executionMode: "concurrent"' in pi
    assert 'role: "ordinary"' in pi
    assert "goal_plus_work_item" in pi
    assert '"bind"' in pi_worker
