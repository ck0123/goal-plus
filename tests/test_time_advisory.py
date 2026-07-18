from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from goal_plus.models import SearchSpec
from goal_plus.runtime import FileSearchRuntime
from goal_plus.time_advisory import (
    build_search_time_advisory,
    is_search_candidate_session,
)
from tests._runtime_helpers import make_project, spec_with_host


def _codex_candidate(tmp_path: Path) -> tuple[FileSearchRuntime, str, str, str]:
    project = make_project(tmp_path)
    payload = spec_with_host(
        project,
        "codex",
        strategy_name="random",
        max_candidates=1,
    ).model_dump(mode="json")
    payload["strategy"]["worker_budget"] = {
        "max_runtime_seconds": 600,
        "max_turns": 8,
        "on_exceed": "interrupt",
    }
    runtime = FileSearchRuntime(tmp_path / ".gp")
    frozen = runtime.freeze_spec(SearchSpec.model_validate(payload), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id, {"goal": "test"})
    return runtime, run_id, task.candidate_id, session.agent_session_id


def test_codex_candidate_identity_accepts_canonical_agent_path(tmp_path: Path) -> None:
    runtime, run_id, _, agent_session_id = _codex_candidate(tmp_path)
    session = runtime._load_agent_session_by_id(agent_session_id, run_id=run_id)
    task_name = str(session.host_handle.task_name)
    rebound = runtime.bind_agent_handle(
        agent_session_id,
        {"host": "codex", "task_name": f"/root/{task_name}"},
    )

    assert is_search_candidate_session(rebound) is True


def test_time_advisory_uses_subagent_iterations_and_lists_candidate_timing(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidate_id, agent_session_id = _codex_candidate(tmp_path)
    session = runtime._load_agent_session_by_id(agent_session_id, run_id=run_id)
    started = datetime.now(timezone.utc) - timedelta(seconds=120)
    runtime._write_agent_session(
        session.model_copy(
            update={
                "created_at": started.replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            }
        )
    )

    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=agent_session_id,
    )
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=agent_session_id,
    )
    runtime.run_verifier(run_id, candidate_id)

    advisory = build_search_time_advisory(
        runtime.root_dir,
        agent_session_id,
        remaining_seconds=30,
    )

    assert advisory is not None
    assert advisory["total_verifier_count"] == 2
    assert advisory["average_submission_seconds"] == pytest.approx(60, abs=2)
    assert advisory["remaining_seconds"] == 30
    assert advisory["deadline_source"] == "host_worker_deadline"
    assert advisory["low_sample"] is False
    assert advisory["candidates"][0]["candidate_id"] == candidate_id
    assert advisory["candidates"][0]["verifier_count"] == 2
    assert "Observed candidate timings" in advisory["message"]
    assert f"- {candidate_id}:" in advisory["message"]
    assert "no action is forced" in advisory["message"]


def test_time_advisory_does_not_fire_when_one_average_submission_still_fits(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidate_id, agent_session_id = _codex_candidate(tmp_path)
    session = runtime._load_agent_session_by_id(agent_session_id, run_id=run_id)
    started = datetime.now(timezone.utc) - timedelta(seconds=90)
    runtime._write_agent_session(
        session.model_copy(
            update={
                "created_at": started.replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            }
        )
    )
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=agent_session_id,
    )

    assert (
        build_search_time_advisory(
            runtime.root_dir,
            agent_session_id,
            remaining_seconds=120,
        )
        is None
    )


def test_time_advisory_uses_outer_deadline_and_marks_one_sample_low_confidence(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidate_id, agent_session_id = _codex_candidate(tmp_path)
    session = runtime._load_agent_session_by_id(agent_session_id, run_id=run_id)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    runtime._write_agent_session(
        session.model_copy(
            update={
                "created_at": (now - timedelta(seconds=75))
                .isoformat()
                .replace("+00:00", "Z")
            }
        )
    )
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=agent_session_id,
    )

    advisory = build_search_time_advisory(
        runtime.root_dir,
        agent_session_id,
        outer_deadline_at=(now + timedelta(seconds=5)).isoformat(),
        now_epoch=now.timestamp(),
    )

    assert advisory is not None
    assert advisory["deadline_source"] == "outer_deadline"
    assert advisory["remaining_seconds"] == 5
    assert advisory["low_sample"] is True
    assert "low confidence" in advisory["message"]
