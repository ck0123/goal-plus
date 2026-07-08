from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentic_any_search_mcp.models import SearchSpec
from agentic_any_search_mcp.pi_worker import run_pi_rpc_worker
from agentic_any_search_mcp.runtime import FileSearchRuntime


ROOT = Path(__file__).resolve().parents[2]
K_MODULE = ROOT / "tests" / "st" / "fixtures" / "k_module_problem"


def _event_log_tool_calls(handle: dict, *, min_agent_starts: int = 1) -> list[tuple[str, dict]]:
    event_log = Path(handle["metadata"]["event_log"])
    calls: list[tuple[str, dict]] = []
    agent_starts = 0
    thinking_levels: set[str] = set()
    state_session_ids: set[str] = set()
    for line in event_log.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "agent_start":
            agent_starts += 1
        if event.get("type") == "response" and event.get("command") == "get_state":
            data = event.get("data") or {}
            if data.get("thinkingLevel"):
                thinking_levels.add(data["thinkingLevel"])
            if data.get("sessionId"):
                state_session_ids.add(data["sessionId"])
        if event.get("type") == "tool_execution_start":
            calls.append((event.get("toolName", ""), event.get("args") or {}))
    assert agent_starts >= min_agent_starts
    assert thinking_levels <= {"high"}
    assert state_session_ids <= {handle["external_id"]}
    return calls


def _assert_gp_worker_artifacts(
    runtime: FileSearchRuntime,
    run_id: str,
    candidate_id: str,
    handle: dict,
    *,
    min_agent_starts: int = 1,
) -> None:
    calls = _event_log_tool_calls(handle, min_agent_starts=min_agent_starts)
    record = runtime._load_candidate_record(run_id, candidate_id)
    workspace = Path(record.task.workspace).resolve()

    def allowed_mutation_path(raw_path: str | None) -> bool:
        if not raw_path:
            return False
        path = Path(raw_path)
        resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
        return resolved == workspace / "initial_program.py" or resolved.is_relative_to(
            workspace / ".tmp"
        )

    assert calls[0][0] == "search_get_agent_context"
    assert any(name == "search_run_verifier" for name, _args in calls)
    edit_indexes = [
        index
        for index, (name, args) in enumerate(calls)
        if name in {"edit", "write"}
        and Path(args.get("path", "")).name == "initial_program.py"
    ]
    assert edit_indexes
    assert any(
        index > edit_indexes[0] and name == "search_run_verifier"
        for index, (name, _args) in enumerate(calls)
    )
    assert not [
        (name, args)
        for name, args in calls
        if name in {"edit", "write"} and not allowed_mutation_path(args.get("path"))
    ]
    assert "initial_program.py" in record.detected_changed_files
    assert record.touched_denied_files is False
    assert record.changed_outside_allowed is False


def _pi_spec(*, max_runtime_seconds: int = 300) -> SearchSpec:
    data = {
        "objective": "maximize k-module pipeline configuration score",
        "metric_name": "combined_score",
        "metric_direction": "maximize",
        "source_path": str(K_MODULE),
        "edit_surface": {
            "allow": ["initial_program.py"],
            "deny": ["evaluator.py", "config.yaml"],
        },
        "budget": {"max_candidates": 1, "max_parallel": 1},
        "process_verifiers": [
            {
                "name": "k_module_score",
                "role": "ranking_signal",
                "command": [
                    "python",
                    "-c",
                    "from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), sort_keys=True))",
                ],
                "timeout_seconds": 30,
            }
        ],
        "strategy": {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {
                "max_runtime_seconds": max_runtime_seconds,
                "max_turns": 8,
                "on_exceed": "interrupt",
            },
        },
    }
    return SearchSpec.model_validate(data)


def _start_pi_candidate(
    runtime: FileSearchRuntime,
    *,
    max_runtime_seconds: int = 300,
):
    frozen = runtime.freeze_spec(_pi_spec(max_runtime_seconds=max_runtime_seconds), [K_MODULE / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(
        run_id,
        task.candidate_id,
        {
            "goal": (
                "Read evaluator.py, find HIDDEN_TARGET, update initial_program.py "
                "configure_pipeline to match it exactly, then run the verifier."
            )
        },
    )
    return run_id, task, session


@pytest.mark.st
@pytest.mark.st_pi_rpc
def test_pi_rpc_k_module(st_project_root: Path) -> None:
    runtime = FileSearchRuntime(st_project_root / ".search")
    run_id, task, session = _start_pi_candidate(runtime)

    handle = run_pi_rpc_worker(
        session.launch,
        pi_binary=os.environ.get("ST_PI_BINARY", "pi"),
        model_pattern=os.environ.get("ST_PI_MODEL"),
        thinking_level=os.environ.get("ST_PI_THINKING"),
    )
    runtime.bind_agent_handle(session.agent_session_id, handle)

    score = runtime.run_verifier(run_id, task.candidate_id)

    _assert_gp_worker_artifacts(runtime, run_id, task.candidate_id, handle)
    assert score.aggregate_score == 1.0


@pytest.mark.st
@pytest.mark.st_pi_rpc
@pytest.mark.skipif(
    os.environ.get("ST_PI_RPC_EXTENDED") != "1",
    reason="set ST_PI_RPC_EXTENDED=1 to run Pi timeout/redispatch smoke",
)
def test_pi_rpc_redispatch_after_timeout(st_project_root: Path) -> None:
    runtime = FileSearchRuntime(st_project_root / ".search")
    run_id, task, session = _start_pi_candidate(runtime, max_runtime_seconds=1)

    timed_out = run_pi_rpc_worker(
        session.launch,
        pi_binary=os.environ.get("ST_PI_BINARY", "pi"),
        model_pattern=os.environ.get("ST_PI_MODEL"),
        thinking_level=os.environ.get("ST_PI_THINKING"),
    )
    runtime.bind_agent_handle(session.agent_session_id, timed_out)
    assert timed_out["metadata"]["timed_out"] is True

    redispatched = runtime.redispatch_candidate(
        run_id,
        task.candidate_id,
        {"goal": "Resume from runtime context and finish the k_module fix."},
        worker_budget={"max_runtime_seconds": 300, "max_turns": 8, "on_exceed": "interrupt"},
    )
    handle = run_pi_rpc_worker(
        redispatched.launch,
        pi_binary=os.environ.get("ST_PI_BINARY", "pi"),
        model_pattern=os.environ.get("ST_PI_MODEL"),
        thinking_level=os.environ.get("ST_PI_THINKING"),
    )
    runtime.bind_agent_handle(redispatched.agent_session_id, handle)

    assert redispatched.agent_session_id != session.agent_session_id
    _assert_gp_worker_artifacts(runtime, run_id, task.candidate_id, handle)
    assert runtime.run_verifier(run_id, task.candidate_id).aggregate_score == 1.0


@pytest.mark.st
@pytest.mark.st_pi_rpc
@pytest.mark.skipif(
    os.environ.get("ST_PI_RPC_EXTENDED") != "1",
    reason="set ST_PI_RPC_EXTENDED=1 to run Pi same-session continue smoke",
)
def test_pi_rpc_continue_restarts_same_session_id(st_project_root: Path) -> None:
    runtime = FileSearchRuntime(st_project_root / ".search")
    run_id, task, session = _start_pi_candidate(runtime)
    handle = run_pi_rpc_worker(
        session.launch,
        pi_binary=os.environ.get("ST_PI_BINARY", "pi"),
        model_pattern=os.environ.get("ST_PI_MODEL"),
        thinking_level=os.environ.get("ST_PI_THINKING"),
    )
    runtime.bind_agent_handle(session.agent_session_id, handle)

    continued = runtime.continue_agent_session(
        session.agent_session_id,
        {"goal": "Call search_get_agent_context, inspect current score, and run verifier again."},
    )
    continued_handle = run_pi_rpc_worker(
        continued.launch,
        pi_binary=os.environ.get("ST_PI_BINARY", "pi"),
        model_pattern=os.environ.get("ST_PI_MODEL"),
        thinking_level=os.environ.get("ST_PI_THINKING"),
    )
    runtime.bind_agent_handle(continued.agent_session_id, continued_handle)

    assert continued.agent_session_id == session.agent_session_id
    assert continued_handle["external_id"] == handle["external_id"]
    assert continued.launch["continuation"] == "session_jsonl_restart"
    _assert_gp_worker_artifacts(
        runtime,
        run_id,
        task.candidate_id,
        continued_handle,
        min_agent_starts=2,
    )
    assert runtime.run_verifier(run_id, task.candidate_id).aggregate_score == 1.0
