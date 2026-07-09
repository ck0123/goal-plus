from __future__ import annotations

from datetime import datetime
import time
from pathlib import Path
from typing import Any

from agentic_any_search_mcp.models import (
    AgentSessionRecord,
    CandidateRecord,
    FrozenSpec,
    GoalPlusRecord,
    IterationRecord,
    RunRecord,
)
from agentic_any_search_mcp.paths import DEFAULT_RUNTIME_ROOT
from agentic_any_search_mcp.runtime import load_json, utc_timestamp, utc_timestamp_from_epoch


def _path_mtime(path: str | None) -> float | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.exists():
        return None
    return candidate.stat().st_mtime


def _path_info(path: str | None) -> dict[str, Any]:
    if not path:
        return {"path": None, "exists": False, "last_updated_at": None, "size_bytes": None}
    candidate = Path(path)
    if not candidate.exists():
        return {"path": path, "exists": False, "last_updated_at": None, "size_bytes": None}
    stat = candidate.stat()
    return {
        "path": path,
        "exists": True,
        "last_updated_at": utc_timestamp_from_epoch(stat.st_mtime),
        "size_bytes": stat.st_size,
    }


def _parse_utc_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _latest_mtime(paths: list[str | None]) -> float | None:
    values = [_path_mtime(path) for path in paths]
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _latest_run_id(root_dir: Path) -> str | None:
    run_paths = sorted(
        root_dir.glob("runs/*/run.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not run_paths:
        return None
    return RunRecord.model_validate(load_json(run_paths[0])).run_id


def _run_dir(root_dir: Path, run_id: str) -> Path:
    path = root_dir / "runs" / run_id
    if not path.exists():
        raise FileNotFoundError(f"search run not found: {run_id}")
    return path


def _load_goal_record(root_dir: Path, goal_plus_id: str) -> GoalPlusRecord:
    path = root_dir / "goal-plus" / goal_plus_id / "goal.json"
    if not path.exists():
        raise FileNotFoundError(f"goal-plus record not found: {goal_plus_id}")
    return GoalPlusRecord.model_validate(load_json(path))


def _load_candidates(run_dir: Path) -> list[CandidateRecord]:
    candidate_dir = run_dir / "candidates"
    if not candidate_dir.exists():
        return []
    return [
        CandidateRecord.model_validate(load_json(path))
        for path in sorted(candidate_dir.glob("*/candidate.json"))
    ]


def _load_agent_sessions(run_dir: Path) -> list[AgentSessionRecord]:
    session_dir = run_dir / "agent_sessions"
    if not session_dir.exists():
        return []
    return [
        AgentSessionRecord.model_validate(load_json(path))
        for path in sorted(session_dir.glob("agent_*.json"))
    ]


def _best_iteration(
    candidate: CandidateRecord,
    metric_direction: str,
) -> IterationRecord | None:
    scored = [
        iteration
        for iteration in candidate.iterations
        if iteration.process_passed is not False
        and iteration.score is not None
        and not iteration.touched_denied_files
        and not iteration.changed_outside_allowed
    ]
    if not scored:
        return None
    reverse = metric_direction == "maximize"
    return sorted(scored, key=lambda iteration: iteration.score, reverse=reverse)[0]


def _goal_payload(record: GoalPlusRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    linked = record.linked_search.model_dump(mode="json") if record.linked_search else None
    next_action = record.next_action.model_dump(mode="json") if record.next_action else None
    return {
        "goal_plus_id": record.goal_plus_id,
        "status": record.status,
        "phase": record.phase,
        "raw_goal": record.raw_goal,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "linked_search": linked,
        "next_action": next_action,
        "hook_counters": record.hook_counters,
    }


def _usage_cost(metrics: dict[str, Any]) -> float:
    usage_total = metrics.get("usage_total")
    if isinstance(usage_total, dict):
        value = usage_total.get("costTotal")
        if isinstance(value, int | float):
            return float(value)
    session_stats = metrics.get("session_stats")
    if isinstance(session_stats, dict):
        value = session_stats.get("cost")
        if isinstance(value, int | float):
            return float(value)
    return 0.0


def _context_payload(metrics: dict[str, Any]) -> dict[str, Any]:
    session_stats = metrics.get("session_stats")
    context = session_stats.get("contextUsage") if isinstance(session_stats, dict) else None
    if isinstance(context, dict):
        return {
            "tokens": context.get("tokens"),
            "context_window": context.get("contextWindow"),
            "percent": context.get("percent"),
            "source": "pi_session_stats",
        }
    tokens = session_stats.get("tokens") if isinstance(session_stats, dict) else None
    if isinstance(tokens, dict):
        return {
            "tokens": tokens.get("total"),
            "context_window": None,
            "percent": None,
            "source": "pi_session_tokens_total",
        }
    return {"tokens": None, "context_window": None, "percent": None, "source": "unknown"}


def _session_liveness(
    candidate: CandidateRecord | None,
    *,
    latest_output_mtime: float | None,
    now: float,
    stale_after_seconds: int,
    timed_out: bool,
) -> str:
    if candidate and candidate.status == "failed":
        return "failed"
    if candidate and candidate.status == "evaluated":
        return "evaluated"
    if timed_out:
        return "timed_out"
    if latest_output_mtime is not None and now - latest_output_mtime > stale_after_seconds:
        return "stale"
    return "running_or_waiting"


def goal_plus_monitor_snapshot(
    root_dir: Path | str = DEFAULT_RUNTIME_ROOT,
    *,
    goal_plus_id: str | None = None,
    run_id: str | None = None,
    stale_after_seconds: int = 600,
) -> dict[str, Any]:
    """Read-only monitoring snapshot for Goal Plus/Search runs.

    This function intentionally does not wait for workers, inspect live
    processes, or mutate runtime state. It summarizes durable `.gp` and
    host-handle evidence so a monitoring agent can poll cheaply.
    """

    root = Path(root_dir).resolve()
    now = time.time()
    warnings: list[dict[str, Any]] = []
    goal_record: GoalPlusRecord | None = None
    if goal_plus_id:
        goal_record = _load_goal_record(root, goal_plus_id)
        linked = goal_record.linked_search
        if run_id is None and linked and linked.run_id:
            run_id = linked.run_id

    if run_id is None and goal_plus_id is None:
        run_id = _latest_run_id(root)
        if run_id:
            warnings.append({"kind": "inferred_latest_run", "run_id": run_id})
    elif run_id is None and goal_record is not None:
        warnings.append(
            {
                "kind": "goal_without_linked_search",
                "goal_plus_id": goal_record.goal_plus_id,
            }
        )

    run_payload: dict[str, Any] | None = None
    candidates_payload: dict[str, dict[str, Any]] = {}
    subagents: list[dict[str, Any]] = []
    main_agent = {
        "elapsed_seconds": None,
        "estimated_cost_total": 0.0,
        "subagent_count": 0,
        "verifier_count": 0,
        "context_tokens_max": None,
        "context_percent_max": None,
    }

    if run_id is not None:
        run_path = _run_dir(root, run_id)
        run = RunRecord.model_validate(load_json(run_path / "run.json"))
        frozen = FrozenSpec.model_validate(
            load_json(root / "specs" / run.frozen_spec_id / "frozen_spec.json")
        )
        candidates = _load_candidates(run_path)
        sessions = _load_agent_sessions(run_path)
        plans_count = len(list((run_path / "plans").glob("*.json")))
        by_candidate = {candidate.candidate_id: candidate for candidate in candidates}
        sessions_by_candidate: dict[str, list[AgentSessionRecord]] = {}
        for session in sessions:
            sessions_by_candidate.setdefault(session.candidate_id, []).append(session)

        created_at_epoch = _parse_utc_timestamp(run.created_at)
        if created_at_epoch is not None:
            main_agent["elapsed_seconds"] = max(0.0, now - created_at_epoch)

        run_payload = {
            "run_id": run.run_id,
            "state": run.state,
            "frozen_spec_id": run.frozen_spec_id,
            "created_at": run.created_at,
            "source_path": run.source_path,
            "plans_count": plans_count,
            "candidates_total": len(candidates),
            "candidates_evaluated": sum(1 for candidate in candidates if candidate.status == "evaluated"),
            "best_candidate_id": run.best_candidate_id,
            "best_score": run.best_score,
            "selected_candidate_id": run.selected_candidate_id,
            "selected_score": run.selected_score,
            "selected_iteration": run.selected_iteration,
            "selected_git_head": run.selected_git_head,
            "budget_used": run.budget_used,
        }

        for candidate in candidates:
            candidate_sessions = sessions_by_candidate.get(candidate.candidate_id, [])
            last_iteration = candidate.iterations[-1] if candidate.iterations else None
            best_iteration = _best_iteration(candidate, frozen.spec.metric_direction)
            candidates_payload[candidate.candidate_id] = {
                "candidate_id": candidate.candidate_id,
                "status": candidate.status,
                "agent_session_count": len(candidate_sessions),
                "verifier_count": len(candidate.iterations),
                "last_score": last_iteration.score if last_iteration else None,
                "last_verifier_at": last_iteration.created_at if last_iteration else None,
                "last_git_head": last_iteration.git_head if last_iteration else None,
                "best_iteration": best_iteration.iteration if best_iteration else None,
                "best_iteration_score": best_iteration.score if best_iteration else None,
                "best_iteration_at": best_iteration.created_at if best_iteration else None,
                "best_iteration_git_head": best_iteration.git_head if best_iteration else None,
                "changed_files": candidate.detected_changed_files,
                "touched_denied_files": candidate.touched_denied_files,
                "changed_outside_allowed": candidate.changed_outside_allowed,
            }
            if not candidate_sessions and candidate.status == "created":
                warnings.append(
                    {
                        "kind": "candidate_without_agent_session",
                        "candidate_id": candidate.candidate_id,
                    }
                )

        for session in sessions:
            candidate = by_candidate.get(session.candidate_id)
            metadata = session.host_handle.metadata
            metrics = metadata.get("pi_metrics") if isinstance(metadata.get("pi_metrics"), dict) else {}
            usage_delta = metrics.get("usage_delta") if isinstance(metrics, dict) else None
            usage_total = metrics.get("usage_total") if isinstance(metrics, dict) else None
            context = _context_payload(metrics if isinstance(metrics, dict) else {})
            event_log = metadata.get("event_log") if isinstance(metadata.get("event_log"), str) else None
            text_log = metadata.get("text_log") if isinstance(metadata.get("text_log"), str) else None
            session_file = metadata.get("session_file") if isinstance(metadata.get("session_file"), str) else None
            latest_output_mtime = _latest_mtime([event_log, text_log, session_file])
            timed_out = bool(metadata.get("timed_out"))
            session_iterations = [
                iteration
                for iteration in (candidate.iterations if candidate else [])
                if iteration.agent_session_id == session.agent_session_id
            ]
            liveness = _session_liveness(
                candidate,
                latest_output_mtime=latest_output_mtime,
                now=now,
                stale_after_seconds=stale_after_seconds,
                timed_out=timed_out,
            )
            main_agent["estimated_cost_total"] = float(main_agent["estimated_cost_total"]) + _usage_cost(
                metrics if isinstance(metrics, dict) else {}
            )
            if isinstance(context.get("tokens"), int | float):
                current = main_agent["context_tokens_max"]
                main_agent["context_tokens_max"] = max(current or 0, context["tokens"])  # type: ignore[arg-type]
            if isinstance(context.get("percent"), int | float):
                current_percent = main_agent["context_percent_max"]
                main_agent["context_percent_max"] = max(current_percent or 0.0, context["percent"])  # type: ignore[arg-type]
            if timed_out:
                warnings.append(
                    {
                        "kind": "subagent_timed_out",
                        "agent_session_id": session.agent_session_id,
                        "candidate_id": session.candidate_id,
                    }
                )
            if liveness == "stale":
                warnings.append(
                    {
                        "kind": "subagent_stale",
                        "agent_session_id": session.agent_session_id,
                        "candidate_id": session.candidate_id,
                    }
                )
            subagents.append(
                {
                    "agent_session_id": session.agent_session_id,
                    "candidate_id": session.candidate_id,
                    "host": session.host,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "attempt_count": len(sessions_by_candidate.get(session.candidate_id, [])),
                    "verifier_count": len(candidate.iterations) if candidate else 0,
                    "session_verifier_count": len(session_iterations),
                    "last_score": candidate.iterations[-1].score if candidate and candidate.iterations else None,
                    "last_verifier_at": (
                        candidate.iterations[-1].created_at if candidate and candidate.iterations else None
                    ),
                    "duration_seconds": metrics.get("duration_seconds") if isinstance(metrics, dict) else None,
                    "usage_delta": usage_delta if isinstance(usage_delta, dict) else None,
                    "usage_total": usage_total if isinstance(usage_total, dict) else None,
                    "context": context,
                    "event_log": _path_info(event_log),
                    "text_log": _path_info(text_log),
                    "session_file": _path_info(session_file),
                    "timed_out": timed_out,
                    "liveness": liveness,
                }
            )

        main_agent["subagent_count"] = len(sessions)
        main_agent["verifier_count"] = sum(len(candidate.iterations) for candidate in candidates)

    return {
        "ok": True,
        "snapshot_at": utc_timestamp(),
        "root_dir": str(root),
        "goal_plus": _goal_payload(goal_record),
        "run": run_payload,
        "main_agent": main_agent,
        "candidates": candidates_payload,
        "subagents": subagents,
        "warnings": warnings,
    }
