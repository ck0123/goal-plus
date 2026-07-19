from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from goal_plus.pi_tool import call_pi_tool
from goal_plus.runtime import FileSearchRuntime

from tests._runtime_helpers import make_project, spec_for


pytestmark = pytest.mark.pi


def test_pi_tool_calls_context_verifier_and_iterations(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime_root = tmp_path / ".search"
    runtime = FileSearchRuntime(runtime_root)
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)

    context = call_pi_tool(
        runtime_root,
        "search_get_agent_context",
        {"agent_session_id": session.agent_session_id},
    )
    assert context["workspace"] == str(task.workspace)
    assert context["candidate_id"] == task.candidate_id

    (task.workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    report = call_pi_tool(
        runtime_root,
        "search_run_verifier",
        {
            "run_id": run_id,
            "candidate_id": task.candidate_id,
            "agent_session_id": session.agent_session_id,
        },
    )
    assert report["candidate_id"] == task.candidate_id

    iterations = call_pi_tool(
        runtime_root,
        "search_list_iterations",
        {"run_id": run_id, "candidate_id": task.candidate_id},
    )
    assert iterations[0]["agent_session_id"] == session.agent_session_id


@pytest.mark.parametrize(
    ("tool_name", "function_name", "arguments", "expected"),
    [
        (
            "pi_search_pool_open",
            "open_pi_search_pool",
            {"run_id": "run_1", "candidate_ids": ["c001"], "max_parallel": 1},
            {"run_id": "run_1", "candidate_ids": ["c001"], "worker_budgets": None, "final_verify": True, "max_parallel": 1},
        ),
        (
            "pi_search_pool_wait_any",
            "wait_any_pi_search_pool",
            {"pool_id": "pool_1", "timeout_seconds": 5},
            {"pool_id": "pool_1", "timeout_seconds": 5},
        ),
        (
            "pi_search_pool_snapshot",
            "snapshot_pi_search_pool",
            {"pool_id": "pool_1"},
            {"pool_id": "pool_1", "run_id": None},
        ),
        (
            "pi_search_pool_continue",
            "continue_pi_search_pool",
            {"pool_id": "pool_1", "candidate_id": "c001"},
            {"pool_id": "pool_1", "candidate_id": "c001", "worker_budget": None, "final_verify": True},
        ),
        (
            "pi_search_pool_close",
            "close_pi_search_pool",
            {"pool_id": "pool_1", "mode": "drain", "timeout_seconds": 5},
            {"pool_id": "pool_1", "mode": "drain", "timeout_seconds": 5},
        ),
    ],
)
def test_pi_tool_dispatches_managed_pool_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_name: str,
    function_name: str,
    arguments: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    calls: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(f"goal_plus.pi_tool.{function_name}", fake)
    assert call_pi_tool(tmp_path / ".search", tool_name, arguments) == {"ok": True}
    assert calls == [{"root_dir": tmp_path / ".search", **expected}]


@pytest.mark.parametrize(
    "tool_name",
    [
        "search_abort_agent_session",
        "pi_search_run_candidate",
        "pi_search_run_batch",
        "pi_search_pool_submit",
    ],
)
def test_pi_tool_rejects_removed_tools(tmp_path: Path, tool_name: str) -> None:
    with pytest.raises(ValueError, match="unsupported pi tool"):
        call_pi_tool(tmp_path / ".search", tool_name, {})
