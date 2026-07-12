from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from goal_plus.models import SearchSpec
from goal_plus.pi_driver import run_pi_search_candidate
from goal_plus.pi_worker import _workspace_progress_handoff, run_pi_rpc_worker
from goal_plus.runtime import FileSearchRuntime


ROOT = Path(__file__).resolve().parents[2]
K_MODULE = ROOT / "tests" / "st" / "fixtures" / "k_module_problem"
CIRCLE_PACKING = ROOT / "tests" / "st" / "fixtures" / "circle_packing"


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
            calls.append((event.get("tool_name", ""), {}))
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

    assert calls[0][0] == "search_get_agent_context"
    assert any(name == "search_run_verifier" for name, _args in calls)
    edit_indexes = [
        index
        for index, (name, _args) in enumerate(calls)
        if name in {"edit", "write"}
    ]
    assert edit_indexes
    assert any(
        index > edit_indexes[0] and name == "search_run_verifier"
        for index, (name, _args) in enumerate(calls)
    )
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


def _circle_packing_pi_spec(*, max_runtime_seconds: int = 300) -> SearchSpec:
    data = json.loads((CIRCLE_PACKING / "spec.json").read_text(encoding="utf-8"))
    data["source_path"] = str(CIRCLE_PACKING)
    data["strategy"] = {
        "name": "random",
        "driver": "builtin",
        "worker_mode": "agent-session-pool",
        "worker_host": "pi-rpc",
        "worker_budget": {
            "max_runtime_seconds": max_runtime_seconds,
            "max_turns": 8,
            "on_exceed": "interrupt",
        },
        "config": {"seed": 42},
    }
    data["budget"] = {"max_candidates": 4, "max_parallel": 2}
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


def _candidate_summary(runtime: FileSearchRuntime, run_id: str) -> list[dict]:
    return [
        {
            "candidate_id": record.candidate_id,
            "status": record.status,
            "score": (
                record.score_report.aggregate_score
                if record.score_report is not None
                else None
            ),
            "iterations": len(record.iterations),
        }
        for record in runtime._load_candidate_records(run_id)
    ]


@pytest.mark.st
@pytest.mark.st_pi_rpc
def test_pi_rpc_circle_packing_two_batch(st_project_root: Path) -> None:
    runtime = FileSearchRuntime(st_project_root / ".search")
    spec = _circle_packing_pi_spec(
        max_runtime_seconds=int(os.environ.get("ST_PI_CYCLE_WORKER_SECONDS", "300"))
    )
    frozen = runtime.freeze_spec(spec, [CIRCLE_PACKING / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    directives = [
        "Implement a hexagonal lattice for 26 circles; edit initial_program.py and run the verifier.",
        "Implement a square-grid shrink-to-fit packing for 26 circles; edit initial_program.py and run the verifier.",
        "Implement concentric rings for 26 circles; edit initial_program.py and run the verifier.",
        "Implement a boundary-hugging packing for 26 circles; edit initial_program.py and run the verifier.",
    ]
    completed_results: list[dict] = []

    for round_index in (1, 2):
        plan = runtime.plan_next(run_id, requested_k=2)
        tasks = runtime.start_batch(run_id, plan.plan_id)
        assert len(tasks) == 2
        assert plan.planned_k == 2

        for task in tasks:
            directive = {
                "goal": directives[len(completed_results)],
                "round_index": round_index,
                "batch_size": 2,
            }
            result = run_pi_search_candidate(
                root_dir=runtime.root_dir,
                run_id=run_id,
                candidate_id=task.candidate_id,
                directive=directive,
                final_verify=True,
                pi_binary=os.environ.get("ST_PI_BINARY", "pi"),
                model_pattern=os.environ.get("ST_PI_MODEL"),
                thinking_level=os.environ.get("ST_PI_THINKING"),
            )
            assert [step["tool"] for step in result["steps"]] == [
                "search_start_agent_session",
                "pi_rpc_run_worker",
                "search_bind_agent_handle",
                "search_run_verifier",
            ]
            completed_results.append(result)

    selection = runtime.select(run_id)
    report_path = runtime.report(run_id)
    candidates = _candidate_summary(runtime, run_id)

    assert len(completed_results) == 4
    assert [result["candidate_id"] for result in completed_results] == [
        "c001",
        "c002",
        "c003",
        "c004",
    ]
    assert len(candidates) == 4
    assert all(candidate["status"] == "evaluated" for candidate in candidates)
    assert all(candidate["iterations"] >= 1 for candidate in candidates)
    assert selection["selected_candidate_id"]
    assert report_path.exists()


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
    frozen = runtime.freeze_spec(
        _pi_spec(max_runtime_seconds=150),
        [K_MODULE / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    def interrupted_worker(launch: dict, **_kwargs: object) -> dict:
        workspace = Path(launch["cwd"])
        program = workspace / "initial_program.py"
        program.write_text(
            program.read_text(encoding="utf-8").replace(
                '"loader": "json_reader"', '"loader": "csv_reader"'
            ),
            encoding="utf-8",
        )
        handoff_path = workspace / ".tmp" / "handoff.json"
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.write_text(
            json.dumps(
                {
                    "summary": "matched loader; three modules remain",
                    "what_was_tried": ["updated loader to csv_reader"],
                    "blockers": ["worker deadline"],
                    "next_steps": ["inspect evaluator target and finish remaining modules"],
                }
            ),
            encoding="utf-8",
        )
        return {
            "host": "pi-rpc",
            "external_id": launch["session_id"],
            "metadata": {
                "timed_out": True,
                "runner_failed": False,
                "continuation": "state_redispatch",
                "progress_handoff": _workspace_progress_handoff(
                    workspace,
                    root=Path(launch["root"]),
                    run_id=run_id,
                    candidate_id=task.candidate_id,
                    timed_out=True,
                    runner_failed=False,
                    assistant_text=None,
                ),
            },
        }

    first = run_pi_search_candidate(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_id=task.candidate_id,
        directive={"goal": "Start the k_module fix."},
        final_verify=False,
        worker_runner=interrupted_worker,
    )
    assert first["handle"]["metadata"]["timed_out"] is True
    assert runtime.list_iterations(run_id, task.candidate_id) == []

    resumed = run_pi_search_candidate(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_id=task.candidate_id,
        directive={
            "goal": (
                "Resume from context.resume and the existing workspace. Read evaluator.py, "
                "finish the remaining k_module target values, and verify immediately."
            )
        },
        redispatch=True,
        runtime_multiplier=2,
        final_verify=True,
        pi_binary=os.environ.get("ST_PI_BINARY", "pi"),
        model_pattern=os.environ.get("ST_PI_MODEL"),
        thinking_level=os.environ.get("ST_PI_THINKING"),
    )
    handle = resumed["handle"]
    context = runtime.get_agent_context(resumed["agent_session_id"])

    assert resumed["agent_session_id"] != first["agent_session_id"]
    assert resumed["launch"]["budget_control"]["max_runtime_seconds"] == 300
    assert context["resume"]["latest_handoff"]["summary"] == (
        "matched loader; three modules remain"
    )
    _assert_gp_worker_artifacts(runtime, run_id, task.candidate_id, handle)
    assert handle["metadata"]["progress_handoff"]["verifier"]["count"] >= 1
    assert handle["metadata"]["progress_handoff"]["workspace"]["dirty"] is False
    assert resumed["final_score_report"]["aggregate_score"] == 1.0
