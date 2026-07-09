from __future__ import annotations

from pathlib import Path
import time
from typing import Any

import pytest

from agentic_any_search_mcp.models import SearchSpec
from agentic_any_search_mcp.pi_driver import run_pi_search_batch, run_pi_search_candidate
from agentic_any_search_mcp.runtime import FileSearchRuntime
from tests.test_runtime_unit import spec_for


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
                "worker_mode": "agent-session-pool",
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
        directive={"goal": "raise VALUE"},
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


def test_run_pi_search_batch_runs_worker_processes_in_parallel(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        _pi_rpc_spec_with_budget(project, max_candidates=2, max_parallel=2),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    candidates = runtime.start_batch(run_id, plan.plan_id)
    events: list[tuple[str, str, float]] = []

    def fake_worker(launch: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        candidate_id = str(launch["candidate_id"])
        events.append((candidate_id, "start", time.monotonic()))
        time.sleep(0.2)
        events.append((candidate_id, "end", time.monotonic()))
        Path(launch["cwd"], "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
        return {
            "host": "pi-rpc",
            "external_id": launch["session_id"],
            "metadata": {"event_log": f"/tmp/{candidate_id}.jsonl"},
        }

    result = run_pi_search_batch(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_ids=[candidate.candidate_id for candidate in candidates],
        final_verify=False,
        max_parallel=2,
        worker_runner=fake_worker,
    )

    starts = {candidate_id: at for candidate_id, phase, at in events if phase == "start"}
    ends = {candidate_id: at for candidate_id, phase, at in events if phase == "end"}

    assert result["ok"] is True
    assert result["candidate_ids"] == ["c001", "c002"]
    assert [item["ok"] for item in result["results"]] == [True, True]
    assert starts["c002"] < ends["c001"]
    assert starts["c001"] < ends["c002"]
