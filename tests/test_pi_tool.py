from __future__ import annotations

from pathlib import Path

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


def test_pi_tool_rejects_unknown_tool(tmp_path: Path) -> None:
    try:
        call_pi_tool(tmp_path / ".search", "search_abort_agent_session", {})
    except ValueError as exc:
        assert "unsupported pi tool" in str(exc)
    else:
        raise AssertionError("unknown Pi tool should fail")
