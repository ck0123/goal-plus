from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from goal_plus.models import SearchSpec
from goal_plus.monitor import goal_plus_monitor_snapshot
from goal_plus.pi_driver import run_pi_search_candidate
from goal_plus.runtime import FileSearchRuntime
from goal_plus.tools import SearchTools
from tests._runtime_helpers import spec_for


pytestmark = pytest.mark.pi


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "initial_program.py").write_text("VALUE = 0\n", encoding="utf-8")
    (project / "evaluator.py").write_text(
        "import importlib.util\n"
        "import json\n"
        "from pathlib import Path\n"
        "module_path = Path('initial_program.py').resolve()\n"
        "spec = importlib.util.spec_from_file_location('candidate_program', module_path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "assert spec.loader is not None\n"
        "spec.loader.exec_module(module)\n"
        "print(json.dumps({'combined_score': float(module.VALUE)}))\n",
        encoding="utf-8",
    )
    return project


def _pi_rpc_spec(project: Path) -> SearchSpec:
    return SearchSpec.model_validate(
        {
            "objective": "raise VALUE",
            "metric_name": "combined_score",
            "metric_direction": "maximize",
            "source_path": str(project),
            "edit_surface": {
                "allow": ["initial_program.py"],
                "deny": ["evaluator.py"],
            },
            "budget": {
                "max_candidates": 1,
                "max_parallel": 1,
            },
            "process_verifiers": [
                {
                    "name": "score",
                    "role": "ranking_signal",
                    "command": ["python", "evaluator.py"],
                    "timeout_seconds": 30,
                }
            ],
            "strategy": {
                "name": "random",
                "worker_host": "pi-rpc",
                "worker_budget": {
                    "max_runtime_seconds": 600,
                    "max_turns": 8,
                    "on_exceed": "interrupt",
                },
            },
        }
    )


def _pi_rpc_spec_with_budget(
    project: Path,
    *,
    max_candidates: int,
    max_parallel: int,
) -> SearchSpec:
    data = _pi_rpc_spec(project).model_dump(mode="json")
    data["budget"]["max_candidates"] = max_candidates
    data["budget"]["max_parallel"] = max_parallel
    return SearchSpec.model_validate(data)


def test_run_pi_search_candidate_binds_worker_handle_and_final_verifies(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(_pi_rpc_spec(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    candidate = runtime.start_batch(run_id, plan.plan_id)[0]
    observed_launches: list[dict[str, Any]] = []

    def fake_worker(launch: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        observed_launches.append({"launch": launch, "kwargs": kwargs})
        Path(launch["cwd"], "initial_program.py").write_text("VALUE = 7\n", encoding="utf-8")
        return {
            "host": "pi-rpc",
            "external_id": launch["session_id"],
            "metadata": {
                "event_log": "/tmp/pi-rpc-agent.jsonl",
                "pi_metrics": {"duration_seconds": 1.25},
                "assistant_text": "updated VALUE",
            },
        }

    result = run_pi_search_candidate(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        final_verify=True,
        worker_runner=fake_worker,
        pi_binary="fake-pi",
        model_pattern="gpt-test",
    )

    assert [step["tool"] for step in result["steps"]] == [
        "search_start_agent_session",
        "pi_rpc_run_worker",
        "search_bind_agent_handle",
        "search_run_verifier",
    ]
    assert result["ok"] is True
    assert result["run_id"] == run_id
    assert result["candidate_id"] == candidate.candidate_id
    assert result["handle"]["metadata"]["pi_metrics"]["duration_seconds"] == 1.25
    assert result["final_score_report"]["aggregate_score"] == 7.0
    assert result["final_score_report"]["process_passed"] is True

    assert len(observed_launches) == 1
    launch_call = observed_launches[0]
    assert launch_call["launch"]["tool"] == "pi_rpc_worker"
    assert launch_call["launch"]["candidate_id"] == candidate.candidate_id
    assert launch_call["kwargs"]["pi_binary"] == "fake-pi"
    assert launch_call["kwargs"]["model_pattern"] == "gpt-test"

    agent_session_id = result["agent_session_id"]
    stored_session = runtime._load_agent_session_by_id(agent_session_id, run_id=run_id)
    assert stored_session.host_handle.external_id == agent_session_id
    assert stored_session.host_handle.metadata["event_log"] == "/tmp/pi-rpc-agent.jsonl"
    assert stored_session.host_handle.metadata["pi_metrics"]["duration_seconds"] == 1.25

    record = runtime._load_candidate_record(run_id, candidate.candidate_id)
    assert record.score_report is not None
    assert record.score_report.aggregate_score == 7.0
    assert record.iterations[-1].agent_session_id is None
    assert record.iterations[-1].score == 7.0


def test_run_pi_search_candidate_skips_duplicate_final_verify_for_infrastructure_failure(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "if os.environ['GOAL_PLUS_VERIFIER_PHASE'] == 'candidate':\n"
        "    output = Path('.goal-plus-verifiers/generated.bin')\n"
        "    output.parent.mkdir(parents=True, exist_ok=True)\n"
        "    output.write_text('compiled', encoding='utf-8')\n"
        "print(json.dumps({'combined_score': 1.0}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(_pi_rpc_spec(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    candidate = runtime.start_batch(run_id, plan.plan_id)[0]

    def fake_worker(launch: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        Path(launch["cwd"], "initial_program.py").write_text(
            "VALUE = 7\n",
            encoding="utf-8",
        )
        worker_runtime = FileSearchRuntime(runtime.root_dir)
        report = worker_runtime.run_verifier(
            run_id,
            candidate.candidate_id,
            agent_session_id=launch["agent_session_id"],
        )
        assert report.verifier_results[0].failure_class == (
            "VerifierWorkspaceSideEffect"
        )
        return {
            "host": "pi-rpc",
            "external_id": launch["session_id"],
            "metadata": {"assistant_text": "stopped on verifier infrastructure"},
        }

    result = run_pi_search_candidate(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        final_verify=True,
        worker_runner=fake_worker,
    )

    assert result["ok"] is False
    assert result["infrastructure_failure"] is True
    assert result["candidate_action"] == "stop_and_report"
    assert result["failure"]["error_type"] == "VerifierWorkspaceSideEffect"
    assert result["steps"][-1]["status"] == (
        "skipped_duplicate_infrastructure_failure"
    )
    record = runtime._load_candidate_record(run_id, candidate.candidate_id)
    assert len(record.iterations) == 1
    assert record.iterations[0].agent_session_id == result["agent_session_id"]


def test_run_pi_search_candidate_resumes_same_native_session_across_processes(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(_pi_rpc_spec(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    candidate = runtime.start_batch(run_id, plan.plan_id)[0]

    launches: list[dict[str, Any]] = []

    def fake_worker(launch: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        launches.append(dict(launch))
        dispatch = len(launches)
        return {
            "host": "pi-rpc",
            "external_id": launch["session_id"],
            "metadata": {
                "continuation": "native_session",
                "process_pid": 1000 + dispatch,
                "pi_metrics": {
                    "final_last_entry_id": f"entry_{dispatch}",
                    "final_entry_count": dispatch,
                    "usage_total": {"input": dispatch * 10},
                    "usage_delta": {"input": 10},
                    "duration_seconds": float(dispatch),
                    "dispatch_duration_seconds": 1.0,
                    "started_at": "2026-07-19T00:00:00Z",
                },
            },
        }

    first = run_pi_search_candidate(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        final_verify=False,
        worker_runner=fake_worker,
    )
    resumed = run_pi_search_candidate(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        redispatch=True,
        worker_budget={
            "max_runtime_seconds": 900,
            "max_turns": 8,
            "on_exceed": "interrupt",
        },
        final_verify=False,
        worker_runner=fake_worker,
    )

    assert first["agent_session_id"] == resumed["agent_session_id"]
    assert resumed["steps"][0]["tool"] == "search_continue_agent_session"
    assert resumed["launch"]["continuation"] == "native_session"
    assert resumed["launch"]["session_id"] == first["launch"]["session_id"]
    assert resumed["launch"]["metrics_baseline"]["last_entry_id"] == "entry_1"
    assert resumed["launch"]["budget_control"]["max_runtime_seconds"] == 900
    assert resumed["steps"][0]["worker_budget_override"]["max_runtime_seconds"] == 900
    assert len(runtime._load_agent_sessions(run_id)) == 1
    stored = runtime._load_agent_session_by_id(first["agent_session_id"], run_id=run_id)
    assert stored.host_handle.metadata["dispatch_count"] == 2
    assert [
        dispatch["process_pid"]
        for dispatch in stored.host_handle.metadata["dispatches"]
    ] == [1001, 1002]


def test_run_pi_search_candidate_accepts_explicit_long_worker_budget(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(_pi_rpc_spec(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    candidate = runtime.start_batch(run_id, plan.plan_id)[0]

    def fake_worker(launch: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        return {
            "host": "pi-rpc",
            "external_id": launch["session_id"],
            "metadata": {},
        }

    result = run_pi_search_candidate(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        worker_budget={
            "max_runtime_seconds": 3600,
            "max_turns": 80,
            "on_exceed": "interrupt",
        },
        final_verify=False,
        worker_runner=fake_worker,
    )

    assert result["launch"]["budget_control"]["max_runtime_seconds"] == 3600
    assert result["steps"][0]["worker_budget_override"]["max_runtime_seconds"] == 3600


def test_run_pi_search_candidate_rejects_non_pi_rpc_search_spec(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    candidate = runtime.start_batch(run_id, plan.plan_id)[0]

    with pytest.raises(ValueError, match="worker_host.*pi-rpc"):
        run_pi_search_candidate(
            root_dir=runtime.root_dir,
            run_id=run_id,
            candidate_id=candidate.candidate_id,
            worker_runner=lambda _launch, **_kwargs: {
                "host": "pi-rpc",
                "external_id": "should-not-run",
            },
        )


def test_run_pi_search_candidate_binds_runner_failure_handle(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(_pi_rpc_spec(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    candidate = runtime.start_batch(run_id, plan.plan_id)[0]

    def failed_worker(_launch: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        raise BrokenPipeError("Pi RPC stdout closed " + ("x" * 1_000))

    result = run_pi_search_candidate(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        worker_runner=failed_worker,
    )

    assert result["ok"] is False
    assert result["failure"]["stage"] == "worker_runner"
    assert result["failure"]["error_type"] == "BrokenPipeError"
    assert len(result["failure"]["message"]) <= 503
    assert result["agent_session_id"]
    assert result["final_score_report"] is None

    stored = runtime._load_agent_session_by_id(result["agent_session_id"], run_id=run_id)
    assert stored.host_handle.external_id == result["agent_session_id"]
    assert stored.host_handle.metadata["runner_failed"] is True
    assert stored.host_handle.metadata["failure_stage"] == "worker_runner"
    assert stored.host_handle.metadata["error_type"] == "BrokenPipeError"
    assert stored.host_handle.metadata["progress_handoff"]["status"] == "runner_failed"
    assert stored.host_handle.metadata["progress_handoff"]["workspace"]["git_head"]

    snapshot = goal_plus_monitor_snapshot(root_dir=runtime.root_dir, run_id=run_id)
    [subagent] = snapshot["subagents"]
    assert subagent["runner_failed"] is True
    assert subagent["failure_stage"] == "worker_runner"
    assert subagent["liveness"] == "failed"
    assert any(
        warning["kind"] == "subagent_runner_failed"
        for warning in snapshot["warnings"]
    )


def test_run_pi_search_candidate_returns_both_failures_when_synthetic_bind_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(_pi_rpc_spec(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    candidate = runtime.start_batch(run_id, plan.plan_id)[0]

    def failed_worker(_launch: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        raise BrokenPipeError("Pi RPC stdout closed")

    def failed_bind(
        _self: SearchTools,
        *,
        agent_session_id: str,
        handle: dict[str, Any],
    ) -> dict[str, Any]:
        raise OSError(f"cannot persist {agent_session_id} {handle['host']}")

    monkeypatch.setattr(SearchTools, "search_bind_agent_handle", failed_bind)

    result = run_pi_search_candidate(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        worker_runner=failed_worker,
    )

    assert result["ok"] is False
    assert result["failure"]["stage"] == "worker_runner"
    assert result["handle_bind_failure"]["stage"] == "bind_synthetic_failure_handle"
    assert result["handle_bind_failure"]["error_type"] == "OSError"
    assert result["bound_session"] is None
