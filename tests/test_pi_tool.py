from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from goal_plus.pi_tool import call_pi_tool
from goal_plus.runtime import FileSearchRuntime

from tests.test_runtime_unit import make_project, spec_for


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


def test_pi_tool_dispatches_candidate_driver(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_pi_search_candidate(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "ok": True,
            "run_id": kwargs["run_id"],
            "candidate_id": kwargs["candidate_id"],
            "steps": [{"tool": "search_bind_agent_handle"}],
        }

    monkeypatch.setattr(
        "goal_plus.pi_tool.run_pi_search_candidate",
        fake_run_pi_search_candidate,
    )

    result = call_pi_tool(
        tmp_path / ".search",
        "pi_search_run_candidate",
        {
            "run_id": "run_1",
            "candidate_id": "c001",
            "directive": {"goal": "try candidate"},
            "redispatch": False,
            "runtime_multiplier": None,
            "worker_budget": None,
            "final_verify": True,
            "pi_binary": "fake-pi",
            "model_pattern": "gpt-test",
        },
    )

    assert result["ok"] is True
    assert calls == [
        {
            "root_dir": tmp_path / ".search",
            "run_id": "run_1",
            "candidate_id": "c001",
            "directive": {"goal": "try candidate"},
            "redispatch": False,
            "runtime_multiplier": None,
            "worker_budget": None,
            "final_verify": True,
            "pi_binary": "fake-pi",
            "extension_path": None,
            "thinking_level": None,
            "model_pattern": "gpt-test",
            "provider": None,
            "model_id": None,
        }
    ]


def test_pi_tool_dispatches_batch_driver(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_pi_search_batch(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "ok": True,
            "run_id": kwargs["run_id"],
            "candidate_ids": kwargs["candidate_ids"],
            "results": [],
        }

    monkeypatch.setattr(
        "goal_plus.pi_tool.run_pi_search_batch",
        fake_run_pi_search_batch,
    )

    result = call_pi_tool(
        tmp_path / ".search",
        "pi_search_run_batch",
        {
            "run_id": "run_1",
            "candidate_ids": ["c001", "c002"],
            "directive": {"goal": "try batch"},
            "worker_budgets": None,
            "final_verify": True,
            "max_parallel": 2,
            "pi_binary": "fake-pi",
            "model_pattern": "gpt-test",
        },
    )

    assert result["ok"] is True
    assert calls == [
        {
            "root_dir": tmp_path / ".search",
            "run_id": "run_1",
            "candidate_ids": ["c001", "c002"],
            "directive": {"goal": "try batch"},
            "worker_budgets": None,
            "final_verify": True,
            "max_parallel": 2,
            "pi_binary": "fake-pi",
            "extension_path": None,
            "thinking_level": None,
            "model_pattern": "gpt-test",
            "provider": None,
            "model_id": None,
        }
    ]


@pytest.mark.parametrize(
    ("tool_name", "function_name", "arguments", "expected"),
    [
        (
            "pi_search_pool_open",
            "open_pi_search_pool",
            {"run_id": "run_1", "candidate_ids": ["c001"], "max_parallel": 1},
            {"run_id": "run_1", "candidate_ids": ["c001"], "directive": None, "worker_budgets": None, "final_verify": True, "max_parallel": 1},
        ),
        (
            "pi_search_pool_submit",
            "submit_pi_search_pool",
            {"pool_id": "pool_1", "candidate_id": "c002"},
            {"pool_id": "pool_1", "candidate_id": "c002", "directive": None, "worker_budget": None, "final_verify": True},
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
            {"pool_id": "pool_1", "candidate_id": "c001", "runtime_multiplier": 1.5},
            {"pool_id": "pool_1", "candidate_id": "c001", "directive": None, "worker_budget": None, "runtime_multiplier": 1.5, "final_verify": True},
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


def test_pi_tool_rejects_unknown_tool(tmp_path: Path) -> None:
    try:
        call_pi_tool(tmp_path / ".search", "search_abort_agent_session", {})
    except ValueError as exc:
        assert "unsupported pi tool" in str(exc)
    else:
        raise AssertionError("unknown Pi tool should fail")
