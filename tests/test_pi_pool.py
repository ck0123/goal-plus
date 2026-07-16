from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from typing import Any

import pytest

import goal_plus.pi_pool as pi_pool
from goal_plus.pi_pool import (
    close_pi_search_pool,
    open_pi_search_pool,
    snapshot_pi_search_pool,
    submit_pi_search_pool,
    run_pool_worker,
    wait_any_pi_search_pool,
)
from goal_plus.runtime import FileSearchRuntime, exclusive_file_lock, load_json, utc_timestamp, write_json
from tests.test_pi_driver import _make_project, _pi_rpc_spec_with_budget


pytestmark = pytest.mark.pi


def _planned_candidates(
    runtime: FileSearchRuntime,
    run_id: str,
    count: int,
) -> list[str]:
    plan = runtime.plan_next(run_id, requested_k=count)
    return [task.candidate_id for task in runtime.start_batch(run_id, plan.plan_id)]


def test_pi_pool_wait_any_refills_before_slowest_worker_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        _pi_rpc_spec_with_budget(project, max_candidates=3, max_parallel=2),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    initial = _planned_candidates(runtime, run_id, 2)
    completion_delays = iter((0.05, None, 0.05))
    release_slowest = threading.Event()
    threads: list[threading.Thread] = []

    def fake_launcher(*, root_dir: Path | str, pool_id: str, job_id: str) -> int:
        delay = next(completion_delays)

        def complete() -> None:
            request = load_json(pi_pool._job_dir(root_dir, pool_id, job_id) / "request.json")
            if delay is None:
                release_slowest.wait(timeout=5)
            else:
                time.sleep(delay)
            result = {
                "ok": True,
                "run_id": request["run_id"],
                "candidate_id": request["candidate_id"],
                "agent_session_id": f"agent_{job_id}",
                "steps": [
                    {"tool": "search_bind_agent_handle"},
                    {"tool": "search_run_verifier"},
                ],
                "final_score_report": {"aggregate_score": 1.0, "process_passed": True},
            }
            with exclusive_file_lock(pi_pool._pool_lock_path(root_dir, pool_id)):
                write_json(pi_pool._job_dir(root_dir, pool_id, job_id) / "result.json", result)
                job = pi_pool._load_job(root_dir, pool_id, job_id)
                job.update(
                    {
                        "status": "completed",
                        "finished_at": utc_timestamp(),
                        "error": None,
                    }
                )
                pi_pool._write_job(root_dir, pool_id, job)

        thread = threading.Thread(target=complete, daemon=True)
        thread.start()
        threads.append(thread)
        return os.getpid()

    monkeypatch.setattr(pi_pool, "_launch_pool_job", fake_launcher)
    opened = open_pi_search_pool(
        root_dir=runtime.root_dir,
        run_id=run_id,
        candidate_ids=initial,
        max_parallel=2,
    )
    pool_id = opened["pool_id"]
    rediscovered = snapshot_pi_search_pool(root_dir=runtime.root_dir, run_id=run_id)
    assert [pool["pool_id"] for pool in rediscovered["pools"]] == [pool_id]

    first = wait_any_pi_search_pool(
        root_dir=runtime.root_dir,
        pool_id=pool_id,
        timeout_seconds=1,
        poll_interval_seconds=0.01,
    )

    assert [event["candidate_id"] for event in first["events"]] == [initial[0]]
    assert first["events"][0]["kind"] == "candidate_ready"
    assert first["free_slots"] == 1
    assert first["active_count"] == 1

    replacement = _planned_candidates(runtime, run_id, 1)[0]
    submitted = submit_pi_search_pool(
        root_dir=runtime.root_dir,
        pool_id=pool_id,
        candidate_id=replacement,
    )
    after_refill = snapshot_pi_search_pool(root_dir=runtime.root_dir, pool_id=pool_id)

    assert submitted["candidate_id"] == replacement
    assert after_refill["active_count"] == 2
    assert any(
        job["candidate_id"] == initial[1] and job["status"] == "running"
        for job in after_refill["jobs"]
    )

    release_slowest.set()
    observed = []
    while len(observed) < 2:
        update = wait_any_pi_search_pool(
            root_dir=runtime.root_dir,
            pool_id=pool_id,
            timeout_seconds=1,
            poll_interval_seconds=0.01,
        )
        observed.extend(event["candidate_id"] for event in update["events"])
    assert set(observed) == {initial[1], replacement}

    closed = close_pi_search_pool(
        root_dir=runtime.root_dir,
        pool_id=pool_id,
        mode="drain",
        timeout_seconds=1,
    )
    assert closed["state"] == "closed"
    assert closed["active_count"] == 0
    for thread in threads:
        thread.join(timeout=1)


def test_pi_pool_enforces_frozen_parallel_limit(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        _pi_rpc_spec_with_budget(project, max_candidates=2, max_parallel=1),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)

    with pytest.raises(ValueError, match="exceeds frozen Search limit"):
        open_pi_search_pool(
            root_dir=runtime.root_dir,
            run_id=run_id,
            max_parallel=2,
        )


def test_pi_pool_rejects_work_after_run_invalidation(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        _pi_rpc_spec_with_budget(project, max_candidates=1, max_parallel=1),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    runtime.invalidate_run(
        run_id,
        reason="verifier_infrastructure_failure",
        summary="main agent confirmed verifier infrastructure failure",
        evidence=[{"failure_class": "VerifierWorkspaceSideEffect"}],
    )

    with pytest.raises(RuntimeError, match="invalidated"):
        open_pi_search_pool(root_dir=runtime.root_dir, run_id=run_id)


def test_pi_pool_worker_publishes_candidate_ready_after_driver_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        _pi_rpc_spec_with_budget(project, max_candidates=1, max_parallel=1),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    candidate_id = _planned_candidates(runtime, run_id, 1)[0]
    opened = open_pi_search_pool(root_dir=runtime.root_dir, run_id=run_id)
    pool_id = opened["pool_id"]
    submitted = submit_pi_search_pool(
        root_dir=runtime.root_dir,
        pool_id=pool_id,
        candidate_id=candidate_id,
        _launcher=lambda **_kwargs: os.getpid(),
    )

    def fake_driver(**request: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "run_id": request["run_id"],
            "candidate_id": request["candidate_id"],
            "agent_session_id": "agent_ready",
            "steps": [
                {"tool": "search_start_agent_session"},
                {"tool": "search_bind_agent_handle"},
                {"tool": "search_run_verifier"},
            ],
            "final_score_report": {"aggregate_score": 2.0, "process_passed": True},
        }

    monkeypatch.setattr(pi_pool, "run_pi_search_candidate", fake_driver)
    assert run_pool_worker(
        root_dir=runtime.root_dir,
        pool_id=pool_id,
        job_id=submitted["job_id"],
    ) == 0

    waited = wait_any_pi_search_pool(
        root_dir=runtime.root_dir,
        pool_id=pool_id,
        timeout_seconds=0,
    )
    assert waited["events"][0]["kind"] == "candidate_ready"
    assert waited["events"][0]["agent_session_id"] == "agent_ready"
    assert [step["tool"] for step in waited["events"][0]["result"]["steps"]] == [
        "search_start_agent_session",
        "search_bind_agent_handle",
        "search_run_verifier",
    ]
