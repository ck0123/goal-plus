"""Read-only timing advisory for active Search candidate workers.

The Search runtime records the evidence used here, but host adapters own when
and how an advisory is delivered.  This module intentionally does not wait,
interrupt, or mutate runtime state.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from goal_plus.models import AgentSessionRecord, CandidateRecord


OUTER_DEADLINE_ENV = "GOAL_PLUS_OUTER_DEADLINE_AT"


def _timestamp(value: str | None) -> float | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _deadline_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        numeric = float(value)
    except ValueError:
        return _timestamp(value)
    if numeric > 10_000_000_000:
        numeric /= 1000
    return numeric


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def find_agent_session(
    root_dir: Path | str,
    agent_session_id: str,
) -> AgentSessionRecord | None:
    root = Path(root_dir).resolve()
    matches = sorted(
        (root / "runs").glob(f"*/agent_sessions/{agent_session_id}.json")
    )
    if len(matches) != 1:
        return None
    payload = _load_json(matches[0])
    if payload is None:
        return None
    try:
        return AgentSessionRecord.model_validate(payload)
    except ValueError:
        return None


def is_search_candidate_session(session: AgentSessionRecord) -> bool:
    if session.host == "pi-rpc":
        return str(session.launch.get("role") or "worker") == "worker"
    if session.host != "codex":
        return False
    agent_type = str(session.launch.get("agent_type") or "").replace("-", "_")
    return agent_type.startswith("search_candidate")


def _run_records(
    root: Path,
    run_id: str,
) -> tuple[list[AgentSessionRecord], list[CandidateRecord]]:
    run_dir = root / "runs" / run_id
    sessions: list[AgentSessionRecord] = []
    candidates: list[CandidateRecord] = []
    for path in sorted((run_dir / "agent_sessions").glob("*.json")):
        payload = _load_json(path)
        if payload is None:
            continue
        try:
            sessions.append(AgentSessionRecord.model_validate(payload))
        except ValueError:
            continue
    for path in sorted((run_dir / "candidates").glob("*/candidate.json")):
        payload = _load_json(path)
        if payload is None:
            continue
        try:
            candidates.append(CandidateRecord.model_validate(payload))
        except ValueError:
            continue
    return sessions, candidates


def _candidate_timings(
    sessions: list[AgentSessionRecord],
    candidates: list[CandidateRecord],
) -> list[dict[str, Any]]:
    starts_by_candidate: dict[str, list[tuple[float, str]]] = {}
    for session in sessions:
        created = _timestamp(session.created_at)
        if created is not None:
            starts_by_candidate.setdefault(session.candidate_id, []).append(
                (created, session.created_at)
            )

    timings: list[dict[str, Any]] = []
    for candidate in candidates:
        starts = starts_by_candidate.get(candidate.candidate_id) or []
        subagent_iterations = [
            iteration
            for iteration in candidate.iterations
            if iteration.agent_session_id is not None
            and _timestamp(iteration.created_at) is not None
        ]
        if not starts or not subagent_iterations:
            continue
        first_start_epoch, first_start = min(starts, key=lambda item: item[0])
        last_iteration = max(
            subagent_iterations,
            key=lambda iteration: _timestamp(iteration.created_at) or 0.0,
        )
        last_verifier_epoch = _timestamp(last_iteration.created_at)
        if last_verifier_epoch is None:
            continue
        elapsed = max(0.0, last_verifier_epoch - first_start_epoch)
        if elapsed <= 0:
            continue
        verifier_count = len(subagent_iterations)
        timings.append(
            {
                "candidate_id": candidate.candidate_id,
                "elapsed_seconds": elapsed,
                "verifier_count": verifier_count,
                "average_seconds": elapsed / verifier_count,
                "first_session_at": first_start,
                "last_verifier_at": last_iteration.created_at,
            }
        )
    return timings


def _available_time(
    session: AgentSessionRecord,
    *,
    now_epoch: float,
    remaining_seconds: float | None,
    outer_deadline_at: str | None,
) -> tuple[float, str] | None:
    choices: list[tuple[float, str]] = []
    if remaining_seconds is not None:
        choices.append((max(0.0, float(remaining_seconds)), "host_worker_deadline"))
    else:
        budget = session.launch.get("budget_control")
        max_runtime = budget.get("max_runtime_seconds") if isinstance(budget, dict) else None
        started = _timestamp(session.created_at)
        if isinstance(max_runtime, (int, float)) and started is not None:
            choices.append(
                (max(0.0, started + float(max_runtime) - now_epoch), "worker_budget")
            )

    outer_deadline = _deadline_timestamp(
        outer_deadline_at
        if outer_deadline_at is not None
        else os.environ.get(OUTER_DEADLINE_ENV)
    )
    if outer_deadline is not None:
        choices.append((max(0.0, outer_deadline - now_epoch), "outer_deadline"))
    return min(choices, key=lambda item: item[0]) if choices else None


def _format_duration(seconds: float) -> str:
    rounded = max(0, int(round(seconds)))
    minutes, remainder = divmod(rounded, 60)
    if minutes:
        return f"{minutes}m{remainder:02d}s"
    return f"{remainder}s"


def _message(
    *,
    remaining_seconds: float,
    average_seconds: float,
    timings: list[dict[str, Any]],
    low_sample: bool,
) -> str:
    lines = [
        "Time advisory (informational only): available time "
        f"{_format_duration(remaining_seconds)} is below the observed average "
        f"verifier-submission time {_format_duration(average_seconds)}.",
        "Observed candidate timings:",
    ]
    for timing in timings:
        lines.append(
            f"- {timing['candidate_id']}: "
            f"{_format_duration(float(timing['elapsed_seconds']))} elapsed / "
            f"{timing['verifier_count']} subagent verifier submission(s) = "
            f"{_format_duration(float(timing['average_seconds']))} average"
        )
    if low_sample:
        lines.append("The estimate has only one submission sample; treat it as low confidence.")
    lines.append(
        "Please account for the remaining time. Decide whether to continue the current work "
        "or run one final search_run_verifier and return the best available result; no action "
        "is forced."
    )
    return "\n".join(lines)


def build_search_time_advisory(
    root_dir: Path | str,
    agent_session_id: str,
    *,
    remaining_seconds: float | None = None,
    outer_deadline_at: str | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any] | None:
    """Build an advisory when one more average verifier submission will not fit."""

    root = Path(root_dir).resolve()
    session = find_agent_session(root, agent_session_id)
    if session is None or not is_search_candidate_session(session):
        return None
    current_epoch = (
        datetime.now(timezone.utc).timestamp() if now_epoch is None else float(now_epoch)
    )
    available = _available_time(
        session,
        now_epoch=current_epoch,
        remaining_seconds=remaining_seconds,
        outer_deadline_at=outer_deadline_at,
    )
    if available is None:
        return None
    available_seconds, deadline_source = available
    sessions, candidates = _run_records(root, session.run_id)
    timings = _candidate_timings(sessions, candidates)
    total_verifier_count = sum(
        int(timing["verifier_count"]) for timing in timings
    )
    if total_verifier_count <= 0:
        return None
    average_seconds = sum(
        float(timing["elapsed_seconds"]) for timing in timings
    ) / total_verifier_count
    if average_seconds <= 0 or available_seconds >= average_seconds:
        return None
    low_sample = total_verifier_count == 1
    return {
        "run_id": session.run_id,
        "candidate_id": session.candidate_id,
        "agent_session_id": session.agent_session_id,
        "deadline_source": deadline_source,
        "remaining_seconds": available_seconds,
        "average_submission_seconds": average_seconds,
        "total_verifier_count": total_verifier_count,
        "low_sample": low_sample,
        "candidates": timings,
        "message": _message(
            remaining_seconds=available_seconds,
            average_seconds=average_seconds,
            timings=timings,
            low_sample=low_sample,
        ),
    }
