from __future__ import annotations

from datetime import datetime
import time
from pathlib import Path
from typing import Any

from goal_plus.goal_plus import FileGoalPlusRuntime
from goal_plus.models import (
    AgentSessionRecord,
    CandidateRecord,
    FrozenSpec,
    GoalPlusLinkedSearch,
    GoalPlusRecord,
    IterationRecord,
    RunRecord,
    SearchPlan,
)
from goal_plus.paths import DEFAULT_RUNTIME_ROOT
from goal_plus.runtime import load_json, utc_timestamp, utc_timestamp_from_epoch


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
    return FileGoalPlusRuntime(root_dir).status(goal_plus_id)


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


def _load_plans(run_dir: Path) -> list[SearchPlan]:
    return [
        SearchPlan.model_validate(load_json(path))
        for path in sorted((run_dir / "plans").glob("plan_*.json"))
    ]


_STRATEGY_STATE_KEYS = (
    "generation",
    "generation_index",
    "population",
    "population_size",
    "tree_depth",
    "max_tree_depth",
    "frontier_node",
    "sampling_mode",
    "parent_candidate_id",
    "archive_candidate_ids",
    "inspiration_candidate_ids",
    "selected_worker_agent_type",
    "seed",
    "external_ref",
)


def _strategy_payload(
    frozen: FrozenSpec,
    latest_plan: SearchPlan | None,
) -> dict[str, Any]:
    strategy = frozen.spec.strategy
    latest_plan_payload: dict[str, Any] | None = None
    if latest_plan is not None:
        trace = latest_plan.strategy_trace
        latest_plan_payload = {
            "plan_id": latest_plan.plan_id,
            "status": latest_plan.status,
            "requested_k": latest_plan.requested_k,
            "planned_k": latest_plan.planned_k,
            "started_candidate_ids": latest_plan.started_candidate_ids,
            "selection_rule": trace.get("selection_rule"),
            "state": {
                key: trace[key]
                for key in _STRATEGY_STATE_KEYS
                if key in trace
            },
        }
    return {
        "name": strategy.name,
        "driver": strategy.driver,
        "ref": strategy.ref,
        "worker_mode": strategy.worker_mode,
        "worker_host": strategy.worker_host,
        "worker_agent_type": strategy.worker_agent_type,
        "history_policy": strategy.history_policy.model_dump(mode="json"),
        "latest_plan": latest_plan_payload,
    }


def _best_iteration(
    candidate: CandidateRecord,
    metric_direction: str,
) -> IterationRecord | None:
    scored = [
        iteration
        for iteration in candidate.iterations
        if iteration.process_passed is True
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
    latest_final_check = (
        record.final_checks[-1].model_dump(mode="json") if record.final_checks else None
    )
    return {
        "goal_plus_id": record.goal_plus_id,
        "status": record.status,
        "phase": record.phase,
        "raw_goal": record.raw_goal,
        "goal_revision": record.goal_revision,
        "goal_revisions_total": len(record.goal_revisions),
        "final_check_policy": record.policy.get("final_check", {"mode": "disabled"}),
        "latest_final_check": latest_final_check,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "search_tasks_total": len(record.search_tasks),
        "current_search_run_id": linked.get("run_id") if linked else None,
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
    runner_failed: bool,
) -> str:
    if candidate and candidate.status == "failed":
        return "failed"
    if runner_failed:
        return "failed"
    if candidate and candidate.status == "evaluated":
        return "evaluated"
    if timed_out:
        return "timed_out"
    if latest_output_mtime is not None and now - latest_output_mtime > stale_after_seconds:
        return "stale"
    return "running_or_waiting"


def _search_task_payload(
    root_dir: Path,
    task: GoalPlusLinkedSearch,
    *,
    current_run_id: str | None,
) -> dict[str, Any]:
    payload = task.model_dump(mode="json")
    run_id = task.run_id
    payload.update(
        {
            "is_current": run_id is not None and run_id == current_run_id,
            "run_exists": False,
            "state": None,
            "run_frozen_spec_id": None,
            "frozen_spec_exists": False,
            "strategy": None,
            "planning_rounds_total": 0,
            "started_rounds_total": 0,
            "candidates_total": 0,
            "candidates_evaluated": 0,
            "worker_sessions_total": 0,
            "verifier_runs_total": 0,
            "estimated_cost_total": 0.0,
        }
    )
    if run_id is None:
        return payload
    run_dir = root_dir / "runs" / run_id
    run_path = run_dir / "run.json"
    if not run_path.exists():
        return payload

    run = RunRecord.model_validate(load_json(run_path))
    frozen_path = root_dir / "specs" / run.frozen_spec_id / "frozen_spec.json"
    frozen = FrozenSpec.model_validate(load_json(frozen_path)) if frozen_path.exists() else None
    plans = _load_plans(run_dir)
    candidates = _load_candidates(run_dir)
    sessions = _load_agent_sessions(run_dir)
    estimated_cost_total = 0.0
    for session in sessions:
        metadata = session.host_handle.metadata
        metrics = metadata.get("pi_metrics") if isinstance(metadata.get("pi_metrics"), dict) else {}
        estimated_cost_total += _usage_cost(metrics if isinstance(metrics, dict) else {})
    payload.update(
        {
            "run_exists": True,
            "state": run.state,
            "run_frozen_spec_id": run.frozen_spec_id,
            "frozen_spec_exists": frozen is not None,
            "strategy": (
                {
                    "name": frozen.spec.strategy.name,
                    "driver": frozen.spec.strategy.driver,
                    "worker_mode": frozen.spec.strategy.worker_mode,
                    "worker_host": frozen.spec.strategy.worker_host,
                }
                if frozen is not None
                else None
            ),
            "planning_rounds_total": len(plans),
            "started_rounds_total": sum(1 for plan in plans if plan.status == "started"),
            "candidates_total": len(candidates),
            "candidates_evaluated": sum(
                1 for candidate in candidates if candidate.status == "evaluated"
            ),
            "worker_sessions_total": len(sessions),
            "verifier_runs_total": sum(len(candidate.iterations) for candidate in candidates),
            "estimated_cost_total": estimated_cost_total,
        }
    )
    return payload


def _search_task_aggregate(search_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "search_tasks_total": len(search_tasks),
        "planning_rounds_total": sum(task["planning_rounds_total"] for task in search_tasks),
        "started_rounds_total": sum(task["started_rounds_total"] for task in search_tasks),
        "candidates_total": sum(task["candidates_total"] for task in search_tasks),
        "candidates_evaluated": sum(task["candidates_evaluated"] for task in search_tasks),
        "worker_sessions_total": sum(task["worker_sessions_total"] for task in search_tasks),
        "verifier_runs_total": sum(task["verifier_runs_total"] for task in search_tasks),
        "estimated_cost_total": sum(task["estimated_cost_total"] for task in search_tasks),
    }


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

    current_search_run_id = (
        goal_record.linked_search.run_id
        if goal_record is not None and goal_record.linked_search is not None
        else None
    )
    task_links = list(goal_record.search_tasks) if goal_record is not None else []
    if goal_record is None and run_id is not None:
        run_record = RunRecord.model_validate(load_json(_run_dir(root, run_id) / "run.json"))
        task_links = [
            GoalPlusLinkedSearch(
                frozen_spec_id=run_record.frozen_spec_id,
                run_id=run_record.run_id,
                linked_at=run_record.created_at,
            )
        ]
        current_search_run_id = run_id

    linked_run_ids = {task.run_id for task in task_links if task.run_id is not None}
    if goal_record is not None and run_id is not None and run_id not in linked_run_ids:
        warnings.append(
            {
                "kind": "run_not_linked_to_goal",
                "goal_plus_id": goal_record.goal_plus_id,
                "run_id": run_id,
            }
        )

    search_tasks = [
        _search_task_payload(root, task, current_run_id=current_search_run_id)
        for task in task_links
    ]
    search_task_aggregate = _search_task_aggregate(search_tasks)
    terminal_run_states = {"promoted", "aborted", "failed"}
    for task in search_tasks:
        task_run_id = task.get("run_id")
        if not task["run_exists"]:
            warnings.append(
                {
                    "kind": "linked_search_run_missing",
                    "goal_plus_id": goal_record.goal_plus_id if goal_record else None,
                    "run_id": task_run_id,
                }
            )
            continue
        if task.get("frozen_spec_id") != task.get("run_frozen_spec_id"):
            warnings.append(
                {
                    "kind": "linked_search_spec_mismatch",
                    "run_id": task_run_id,
                    "linked_frozen_spec_id": task.get("frozen_spec_id"),
                    "run_frozen_spec_id": task.get("run_frozen_spec_id"),
                }
            )
        if not task["frozen_spec_exists"]:
            warnings.append(
                {
                    "kind": "linked_search_frozen_spec_missing",
                    "run_id": task_run_id,
                    "frozen_spec_id": task.get("run_frozen_spec_id"),
                }
            )
        if not task["is_current"] and task["state"] not in terminal_run_states:
            warnings.append(
                {
                    "kind": "superseded_search_task_not_terminal",
                    "run_id": task_run_id,
                    "state": task["state"],
                }
            )
    current_task = next((task for task in search_tasks if task["is_current"]), None)
    if (
        goal_record is not None
        and goal_record.status == "complete"
        and current_task is not None
        and current_task["state"] != "promoted"
    ):
        warnings.append(
            {
                "kind": "completed_goal_current_search_not_promoted",
                "goal_plus_id": goal_record.goal_plus_id,
                "run_id": current_task.get("run_id"),
                "state": current_task.get("state"),
            }
        )

    run_payload: dict[str, Any] | None = None
    strategy_payload: dict[str, Any] | None = None
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

    selected_run_exists = run_id is not None and (root / "runs" / run_id / "run.json").exists()
    if run_id is not None and selected_run_exists:
        run_path = _run_dir(root, run_id)
        run = RunRecord.model_validate(load_json(run_path / "run.json"))
        frozen = FrozenSpec.model_validate(
            load_json(root / "specs" / run.frozen_spec_id / "frozen_spec.json")
        )
        candidates = _load_candidates(run_path)
        sessions = _load_agent_sessions(run_path)
        plans = _load_plans(run_path)
        plans_count = len(plans)
        started_rounds_total = sum(1 for plan in plans if plan.status == "started")
        latest_plan = plans[-1] if plans else None
        strategy_payload = _strategy_payload(frozen, latest_plan)
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
            "planning_rounds_total": plans_count,
            "started_rounds_total": started_rounds_total,
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
            runner_failed = bool(metadata.get("runner_failed"))
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
                runner_failed=runner_failed,
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
            if runner_failed:
                warnings.append(
                    {
                        "kind": "subagent_runner_failed",
                        "agent_session_id": session.agent_session_id,
                        "candidate_id": session.candidate_id,
                        "failure_stage": metadata.get("failure_stage"),
                        "error_type": metadata.get("error_type"),
                        "error": metadata.get("error"),
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
                    "runner_failed": runner_failed,
                    "progress_handoff": (
                        metadata.get("progress_handoff")
                        if isinstance(metadata.get("progress_handoff"), dict)
                        else None
                    ),
                    "failure_stage": metadata.get("failure_stage"),
                    "error_type": metadata.get("error_type"),
                    "error": metadata.get("error"),
                    "soft_closeout_seconds": metadata.get("soft_closeout_seconds"),
                    "soft_closeout_sent": bool(metadata.get("soft_closeout_sent")),
                    "raw_logging": bool(metadata.get("raw_logging")),
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
        "selected_run_id": run_id,
        "search_tasks": search_tasks,
        "search_task_aggregate": search_task_aggregate,
        "run": run_payload,
        "strategy": strategy_payload,
        "main_agent": main_agent,
        "candidates": candidates_payload,
        "subagents": subagents,
        "warnings": warnings,
    }
