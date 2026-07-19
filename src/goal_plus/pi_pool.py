from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Literal
import uuid

from goal_plus.agent_pool import WorkerPoolEvent
from goal_plus.pi_driver import run_pi_search_candidate
from goal_plus.runtime import (
    FileSearchRuntime,
    exclusive_file_lock,
    load_json,
    utc_timestamp,
    write_json,
)


PoolCloseMode = Literal["drain", "interrupt"]
ACTIVE_JOB_STATES = {"starting", "running"}
TERMINAL_JOB_STATES = {"completed", "failed", "interrupted"}
POOL_SCHEMA_VERSION = 1


class _PoolWorkerInterrupted(BaseException):
    """Unwind the wrapper so the Pi RPC runner can clean up its child process."""


def _pool_root(root_dir: Path | str) -> Path:
    return Path(root_dir).expanduser().resolve() / "host-pools" / "pi"


def _safe_identifier(value: str, *, label: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if not value or any(ch not in allowed for ch in value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def _pool_dir(root_dir: Path | str, pool_id: str) -> Path:
    return _pool_root(root_dir) / _safe_identifier(pool_id, label="pool_id")


def _pool_path(root_dir: Path | str, pool_id: str) -> Path:
    return _pool_dir(root_dir, pool_id) / "pool.json"


def _pool_lock_path(root_dir: Path | str, pool_id: str) -> Path:
    return _pool_dir(root_dir, pool_id) / "pool.lock"


def _job_dir(root_dir: Path | str, pool_id: str, job_id: str) -> Path:
    return (
        _pool_dir(root_dir, pool_id)
        / "jobs"
        / _safe_identifier(job_id, label="job_id")
    )


def _job_path(root_dir: Path | str, pool_id: str, job_id: str) -> Path:
    return _job_dir(root_dir, pool_id, job_id) / "job.json"


def _load_pool(root_dir: Path | str, pool_id: str) -> dict[str, Any]:
    path = _pool_path(root_dir, pool_id)
    if not path.exists():
        raise FileNotFoundError(f"unknown Pi worker pool: {pool_id}")
    return load_json(path)


def _load_job(root_dir: Path | str, pool_id: str, job_id: str) -> dict[str, Any]:
    path = _job_path(root_dir, pool_id, job_id)
    if not path.exists():
        raise FileNotFoundError(f"unknown Pi pool job: {job_id}")
    return load_json(path)


def _write_pool(root_dir: Path | str, pool: dict[str, Any]) -> None:
    pool["updated_at"] = utc_timestamp()
    write_json(_pool_path(root_dir, str(pool["pool_id"])), pool)


def _write_job(root_dir: Path | str, pool_id: str, job: dict[str, Any]) -> None:
    job["updated_at"] = utc_timestamp()
    write_json(_job_path(root_dir, pool_id, str(job["job_id"])), job)


def _is_process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _validate_pool_run(
    root_dir: Path | str,
    run_id: str,
    max_parallel: int | None,
) -> int:
    runtime = FileSearchRuntime(root_dir)
    run = runtime._load_run(run_id)
    runtime._assert_run_not_invalidated(run, "open or submit Pi pool work")
    frozen = runtime._load_frozen_spec(run.frozen_spec_id)
    if frozen.spec.strategy.worker_host != "pi-rpc":
        raise ValueError(
            "Pi worker pools require SearchSpec strategy.worker_host='pi-rpc'; "
            f"got {frozen.spec.strategy.worker_host!r}"
        )
    frozen_limit = int(frozen.spec.budget.max_parallel)
    selected = frozen_limit if max_parallel is None else int(max_parallel)
    if selected <= 0:
        raise ValueError("max_parallel must be > 0")
    if selected > frozen_limit:
        raise ValueError(
            f"max_parallel {selected} exceeds frozen Search limit {frozen_limit}"
        )
    return selected


def _validate_candidate(root_dir: Path | str, run_id: str, candidate_id: str) -> None:
    runtime = FileSearchRuntime(root_dir)
    record = runtime._load_candidate_record(run_id, candidate_id)
    if record.status not in {"created", "evaluated"}:
        raise RuntimeError(
            f"cannot dispatch candidate {candidate_id} in status {record.status}"
        )


def _resume_agent_session_id(
    root_dir: Path | str,
    *,
    run_id: str,
    candidate_id: str,
    jobs: list[dict[str, Any]],
    pool_id: str,
) -> str:
    for job in reversed(jobs):
        if job.get("candidate_id") != candidate_id:
            continue
        result_path = _job_dir(root_dir, pool_id, str(job["job_id"])) / "result.json"
        if not result_path.exists():
            continue
        result = load_json(result_path)
        agent_session_id = result.get("agent_session_id")
        if isinstance(agent_session_id, str) and agent_session_id:
            return agent_session_id

    runtime = FileSearchRuntime(root_dir)
    sessions = [
        session
        for session in runtime._load_agent_sessions(run_id)
        if session.candidate_id == candidate_id and session.host == "pi-rpc"
    ]
    if sessions:
        return sessions[-1].agent_session_id
    raise RuntimeError(
        f"candidate {candidate_id} has no Pi native session to continue"
    )


def _launch_pool_job(
    *,
    root_dir: Path | str,
    pool_id: str,
    job_id: str,
) -> int:
    job_dir = _job_dir(root_dir, pool_id, job_id)
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    source_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(source_root)
        if not existing_pythonpath
        else os.pathsep.join((str(source_root), existing_pythonpath))
    )
    command = [
        sys.executable,
        "-m",
        "goal_plus.pi_pool",
        "worker",
        "--root",
        str(Path(root_dir).expanduser().resolve()),
        "--pool-id",
        pool_id,
        "--job-id",
        job_id,
    ]
    with stdout_path.open("a", encoding="utf-8") as stdout_handle, stderr_path.open(
        "a", encoding="utf-8"
    ) as stderr_handle:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
            close_fds=True,
            env=env,
        )
    return int(process.pid)


def open_pi_search_pool(
    *,
    root_dir: Path | str,
    run_id: str,
    candidate_ids: list[str] | None = None,
    worker_budgets: dict[str, dict[str, Any]] | None = None,
    final_verify: bool = True,
    max_parallel: int | None = None,
) -> dict[str, Any]:
    selected_parallel = _validate_pool_run(root_dir, run_id, max_parallel)
    initial_ids = list(candidate_ids or [])
    if len(initial_ids) != len(set(initial_ids)):
        raise ValueError("candidate_ids must be unique")
    if len(initial_ids) > selected_parallel:
        raise ValueError(
            f"initial candidate count {len(initial_ids)} exceeds max_parallel {selected_parallel}"
        )
    unknown_budget_ids = sorted(set(worker_budgets or {}) - set(initial_ids))
    if unknown_budget_ids:
        raise ValueError(
            "worker_budgets contains unknown candidate ids: "
            + ", ".join(unknown_budget_ids)
        )
    for candidate_id in initial_ids:
        _validate_candidate(root_dir, run_id, candidate_id)

    pool_id = f"pool_{uuid.uuid4().hex[:12]}"
    now = utc_timestamp()
    pool = {
        "schema_version": POOL_SCHEMA_VERSION,
        "pool_id": pool_id,
        "host": "pi-rpc",
        "run_id": run_id,
        "max_parallel": selected_parallel,
        "state": "open",
        "created_at": now,
        "updated_at": now,
        "jobs": [],
    }
    with exclusive_file_lock(_pool_lock_path(root_dir, pool_id)):
        _write_pool(root_dir, pool)

    submitted = []
    try:
        for candidate_id in initial_ids:
            submitted.append(
                _submit_pi_search_pool(
                    root_dir=root_dir,
                    pool_id=pool_id,
                    candidate_id=candidate_id,
                    worker_budget=(worker_budgets or {}).get(candidate_id),
                    final_verify=final_verify,
                )
            )
    except Exception:
        try:
            close_pi_search_pool(
                root_dir=root_dir,
                pool_id=pool_id,
                mode="interrupt",
                timeout_seconds=5,
            )
        except Exception:
            pass
        raise
    snapshot = snapshot_pi_search_pool(root_dir=root_dir, pool_id=pool_id)
    snapshot["submitted"] = submitted
    return snapshot


def _submit_pi_search_pool(
    *,
    root_dir: Path | str,
    pool_id: str,
    candidate_id: str,
    redispatch: bool = False,
    worker_budget: dict[str, Any] | None = None,
    final_verify: bool = True,
    _launcher: Callable[..., int] | None = None,
) -> dict[str, Any]:
    launcher = _launcher or _launch_pool_job
    with exclusive_file_lock(_pool_lock_path(root_dir, pool_id)):
        pool = _load_pool(root_dir, pool_id)
        if pool["state"] != "open":
            raise RuntimeError(f"cannot submit to Pi pool in state {pool['state']}")
        _validate_candidate(root_dir, str(pool["run_id"]), candidate_id)
        jobs = [_load_job(root_dir, pool_id, job_id) for job_id in pool["jobs"]]
        active = [job for job in jobs if job["status"] in ACTIVE_JOB_STATES]
        if len(active) >= int(pool["max_parallel"]):
            raise RuntimeError(
                f"Pi pool {pool_id} is full ({len(active)}/{pool['max_parallel']})"
            )
        if any(job["candidate_id"] == candidate_id for job in active):
            raise RuntimeError(f"candidate {candidate_id} already has an active pool job")
        if not redispatch and any(job["candidate_id"] == candidate_id for job in jobs):
            raise RuntimeError(
                f"candidate {candidate_id} was already submitted; use pool_continue for continuation"
            )
        resume_agent_session_id = (
            _resume_agent_session_id(
                root_dir,
                run_id=str(pool["run_id"]),
                candidate_id=candidate_id,
                jobs=jobs,
                pool_id=pool_id,
            )
            if redispatch
            else None
        )

        job_id = f"job_{uuid.uuid4().hex[:12]}"
        now = utc_timestamp()
        job = {
            "job_id": job_id,
            "pool_id": pool_id,
            "run_id": pool["run_id"],
            "candidate_id": candidate_id,
            "redispatch": bool(redispatch),
            "continuation": "native_session" if redispatch else "new_session",
            "status": "starting",
            "pid": None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "updated_at": now,
            "delivered_at": None,
            "error": None,
        }
        request = {
            "root_dir": str(Path(root_dir).expanduser().resolve()),
            "run_id": pool["run_id"],
            "candidate_id": candidate_id,
            "redispatch": bool(redispatch),
            "resume_agent_session_id": resume_agent_session_id,
            "worker_budget": worker_budget,
            "final_verify": bool(final_verify),
        }
        job_dir = _job_dir(root_dir, pool_id, job_id)
        write_json(job_dir / "request.json", request)
        _write_job(root_dir, pool_id, job)
        pool["jobs"].append(job_id)
        _write_pool(root_dir, pool)
        try:
            pid = int(
                launcher(
                    root_dir=root_dir,
                    pool_id=pool_id,
                    job_id=job_id,
                )
            )
        except Exception as exc:
            job.update(
                {
                    "status": "failed",
                    "finished_at": utc_timestamp(),
                    "error": {
                        "stage": "launch",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
            )
            _write_job(root_dir, pool_id, job)
            raise
        job.update({"status": "running", "pid": pid, "started_at": utc_timestamp()})
        _write_job(root_dir, pool_id, job)

    return {
        "pool_id": pool_id,
        "job_id": job_id,
        "candidate_id": candidate_id,
        "redispatch": bool(redispatch),
        "continuation": "native_session" if redispatch else "new_session",
        "status": "running",
        "pid": pid,
    }


def continue_pi_search_pool(
    *,
    root_dir: Path | str,
    pool_id: str,
    candidate_id: str,
    worker_budget: dict[str, Any] | None = None,
    final_verify: bool = True,
) -> dict[str, Any]:
    return _submit_pi_search_pool(
        root_dir=root_dir,
        pool_id=pool_id,
        candidate_id=candidate_id,
        redispatch=True,
        worker_budget=worker_budget,
        final_verify=final_verify,
    )


def _reconcile_jobs_locked(
    root_dir: Path | str,
    pool: dict[str, Any],
) -> list[dict[str, Any]]:
    pool_id = str(pool["pool_id"])
    jobs = []
    for job_id in pool["jobs"]:
        job = _load_job(root_dir, pool_id, job_id)
        if job["status"] in ACTIVE_JOB_STATES and not _is_process_alive(job.get("pid")):
            job.update(
                {
                    "status": "failed",
                    "finished_at": utc_timestamp(),
                    "error": {
                        "stage": "supervisor",
                        "error_type": "WorkerProcessExited",
                        "message": "Pi pool worker exited without a terminal result",
                    },
                }
            )
            _write_job(root_dir, pool_id, job)
        jobs.append(job)
    return jobs


def _job_result(
    root_dir: Path | str,
    pool_id: str,
    job: dict[str, Any],
) -> dict[str, Any] | None:
    result_path = _job_dir(root_dir, pool_id, str(job["job_id"])) / "result.json"
    if result_path.exists():
        return load_json(result_path)
    if job.get("error") is not None:
        return {
            "ok": False,
            "failure": job["error"],
            "error": job["error"].get("message"),
        }
    return None


def _snapshot_payload(
    root_dir: Path | str,
    pool: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    active_count = sum(job["status"] in ACTIVE_JOB_STATES for job in jobs)
    terminal_count = sum(job["status"] in TERMINAL_JOB_STATES for job in jobs)
    pool_id = str(pool["pool_id"])
    return {
        "pool_id": pool_id,
        "host": pool["host"],
        "run_id": pool["run_id"],
        "state": pool["state"],
        "max_parallel": pool["max_parallel"],
        "active_count": active_count,
        "free_slots": max(0, int(pool["max_parallel"]) - active_count),
        "terminal_count": terminal_count,
        "undelivered_count": sum(
            job["status"] in TERMINAL_JOB_STATES and not job.get("delivered_at")
            for job in jobs
        ),
        "jobs": [
            {
                **job,
                "result": (
                    _job_result(root_dir, pool_id, job)
                    if job["status"] in TERMINAL_JOB_STATES
                    else None
                ),
            }
            for job in jobs
        ],
        "created_at": pool["created_at"],
        "updated_at": pool["updated_at"],
    }


def snapshot_pi_search_pool(
    *,
    root_dir: Path | str,
    pool_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    if pool_id is None:
        pools = []
        root = _pool_root(root_dir)
        if root.exists():
            for path in sorted(root.glob("pool_*/pool.json")):
                candidate_pool_id = path.parent.name
                snapshot = snapshot_pi_search_pool(
                    root_dir=root_dir,
                    pool_id=candidate_pool_id,
                )
                if run_id is None or snapshot["run_id"] == run_id:
                    pools.append(snapshot)
        return {"run_id": run_id, "pools": pools}
    with exclusive_file_lock(_pool_lock_path(root_dir, pool_id)):
        pool = _load_pool(root_dir, pool_id)
        if run_id is not None and pool["run_id"] != run_id:
            raise ValueError(
                f"Pi pool {pool_id} belongs to run {pool['run_id']}, not {run_id}"
            )
        jobs = _reconcile_jobs_locked(root_dir, pool)
        return _snapshot_payload(root_dir, pool, jobs)


def _event_from_job(
    root_dir: Path | str,
    pool: dict[str, Any],
    job: dict[str, Any],
) -> WorkerPoolEvent:
    status = str(job["status"])
    kind: Literal["candidate_ready", "failed", "interrupted", "timed_out"]
    if status == "completed":
        kind = "candidate_ready"
    elif status == "interrupted":
        kind = "interrupted"
    else:
        kind = "failed"
    result = _job_result(root_dir, str(pool["pool_id"]), job)
    return WorkerPoolEvent(
        event_id=f"event_{job['job_id']}",
        host="pi-rpc",
        pool_id=str(pool["pool_id"]),
        kind=kind,
        run_id=str(pool["run_id"]),
        candidate_id=str(job["candidate_id"]),
        job_id=str(job["job_id"]),
        agent_session_id=(
            str(result["agent_session_id"])
            if isinstance(result, dict) and result.get("agent_session_id")
            else None
        ),
        result=result,
    )


def wait_any_pi_search_pool(
    *,
    root_dir: Path | str,
    pool_id: str,
    timeout_seconds: float = 30,
    poll_interval_seconds: float = 0.2,
) -> dict[str, Any]:
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be >= 0")
    deadline = time.monotonic() + timeout_seconds
    while True:
        with exclusive_file_lock(_pool_lock_path(root_dir, pool_id)):
            pool = _load_pool(root_dir, pool_id)
            jobs = _reconcile_jobs_locked(root_dir, pool)
            ready = [
                job
                for job in jobs
                if job["status"] in TERMINAL_JOB_STATES and not job.get("delivered_at")
            ]
            if ready:
                events = []
                delivered_at = utc_timestamp()
                for job in ready:
                    events.append(_event_from_job(root_dir, pool, job).as_dict())
                    job["delivered_at"] = delivered_at
                    _write_job(root_dir, pool_id, job)
                snapshot = _snapshot_payload(root_dir, pool, jobs)
                return {
                    "pool_id": pool_id,
                    "events": events,
                    "timed_out": False,
                    "active_count": snapshot["active_count"],
                    "free_slots": snapshot["free_slots"],
                    "state": snapshot["state"],
                }
            active_count = sum(job["status"] in ACTIVE_JOB_STATES for job in jobs)
            if active_count == 0:
                return {
                    "pool_id": pool_id,
                    "events": [],
                    "timed_out": False,
                    "active_count": 0,
                    "free_slots": int(pool["max_parallel"]),
                    "state": pool["state"],
                }
        if time.monotonic() >= deadline:
            snapshot = snapshot_pi_search_pool(root_dir=root_dir, pool_id=pool_id)
            return {
                "pool_id": pool_id,
                "events": [],
                "timed_out": True,
                "active_count": snapshot["active_count"],
                "free_slots": snapshot["free_slots"],
                "state": snapshot["state"],
            }
        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


def _signal_process(pid: int, sig: signal.Signals) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def close_pi_search_pool(
    *,
    root_dir: Path | str,
    pool_id: str,
    mode: PoolCloseMode = "drain",
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    if mode not in {"drain", "interrupt"}:
        raise ValueError("mode must be 'drain' or 'interrupt'")
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be >= 0")
    with exclusive_file_lock(_pool_lock_path(root_dir, pool_id)):
        pool = _load_pool(root_dir, pool_id)
        if pool["state"] == "closed":
            jobs = _reconcile_jobs_locked(root_dir, pool)
            return _snapshot_payload(root_dir, pool, jobs)
        pool["state"] = "draining" if mode == "drain" else "interrupting"
        _write_pool(root_dir, pool)
        jobs = _reconcile_jobs_locked(root_dir, pool)
        if mode == "interrupt":
            for job in jobs:
                if job["status"] in ACTIVE_JOB_STATES and job.get("pid"):
                    _signal_process(int(job["pid"]), signal.SIGTERM)

    deadline = time.monotonic() + timeout_seconds
    while True:
        snapshot = snapshot_pi_search_pool(root_dir=root_dir, pool_id=pool_id)
        if snapshot["active_count"] == 0:
            break
        if time.monotonic() >= deadline:
            if mode == "interrupt":
                for job in snapshot["jobs"]:
                    if job["status"] in ACTIVE_JOB_STATES and job.get("pid"):
                        _signal_process(int(job["pid"]), signal.SIGKILL)
                time.sleep(0.2)
                snapshot = snapshot_pi_search_pool(
                    root_dir=root_dir,
                    pool_id=pool_id,
                )
                if snapshot["active_count"] == 0:
                    break
            snapshot["close_timed_out"] = True
            return snapshot
        time.sleep(0.2)

    with exclusive_file_lock(_pool_lock_path(root_dir, pool_id)):
        pool = _load_pool(root_dir, pool_id)
        pool["state"] = "closed"
        _write_pool(root_dir, pool)
        jobs = _reconcile_jobs_locked(root_dir, pool)
        return _snapshot_payload(root_dir, pool, jobs)


def _worker_signal_handler(_signum: int, _frame: Any) -> None:
    raise _PoolWorkerInterrupted()


def run_pool_worker(
    *,
    root_dir: Path | str,
    pool_id: str,
    job_id: str,
) -> int:
    request_path = _job_dir(root_dir, pool_id, job_id) / "request.json"
    request = load_json(request_path)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, _worker_signal_handler)
    signal.signal(signal.SIGINT, _worker_signal_handler)
    try:
        try:
            result = run_pi_search_candidate(**request)
        except _PoolWorkerInterrupted:
            with exclusive_file_lock(_pool_lock_path(root_dir, pool_id)):
                job = _load_job(root_dir, pool_id, job_id)
                job.update(
                    {
                        "status": "interrupted",
                        "finished_at": utc_timestamp(),
                        "error": {
                            "stage": "supervisor",
                            "error_type": "WorkerInterrupted",
                            "message": (
                                "Pi pool worker was interrupted by the supervisor"
                            ),
                        },
                    }
                )
                _write_job(root_dir, pool_id, job)
            return 130
        except BaseException as exc:
            with exclusive_file_lock(_pool_lock_path(root_dir, pool_id)):
                job = _load_job(root_dir, pool_id, job_id)
                job.update(
                    {
                        "status": "failed",
                        "finished_at": utc_timestamp(),
                        "error": {
                            "stage": "pool_worker",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                    }
                )
                _write_job(root_dir, pool_id, job)
            return 1

        with exclusive_file_lock(_pool_lock_path(root_dir, pool_id)):
            write_json(_job_dir(root_dir, pool_id, job_id) / "result.json", result)
            job = _load_job(root_dir, pool_id, job_id)
            job.update(
                {
                    "status": "completed",
                    "finished_at": utc_timestamp(),
                    "error": None,
                }
            )
            _write_job(root_dir, pool_id, job)
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Durable Pi worker-pool supervisor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    worker = subparsers.add_parser("worker", help="Run one detached pool worker")
    worker.add_argument("--root", required=True)
    worker.add_argument("--pool-id", required=True)
    worker.add_argument("--job-id", required=True)
    parsed = parser.parse_args(argv)
    if parsed.command == "worker":
        return run_pool_worker(
            root_dir=parsed.root,
            pool_id=parsed.pool_id,
            job_id=parsed.job_id,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
