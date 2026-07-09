from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_any_search_mcp.pi_tool import call_pi_tool
from agentic_any_search_mcp.runtime import FileSearchRuntime

from tests.test_runtime_unit import make_project, spec_for


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
        "agentic_any_search_mcp.pi_tool.run_pi_search_candidate",
        fake_run_pi_search_candidate,
    )

    result = call_pi_tool(
        tmp_path / ".search",
        "pi_search_run_candidate",
        {
            "run_id": "run_1",
            "candidate_id": "c001",
            "directive": {"goal": "try candidate"},
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
            "final_verify": True,
            "pi_binary": "fake-pi",
            "extension_path": None,
            "thinking_level": None,
            "model_pattern": "gpt-test",
            "provider": None,
            "model_id": None,
        }
    ]


def test_pi_tool_rejects_unknown_tool(tmp_path: Path) -> None:
    try:
        call_pi_tool(tmp_path / ".search", "search_abort_agent_session", {})
    except ValueError as exc:
        assert "unsupported pi tool" in str(exc)
    else:
        raise AssertionError("unknown Pi tool should fail")
