from __future__ import annotations

from collections import Counter
from datetime import datetime
import time
from typing import Any, Callable, Iterable

from goal_plus.models import (
    AgentSessionRecord,
    CandidateRecord,
    FrozenSpec,
    IterationRecord,
    RunRecord,
)


STATISTICS_SCHEMA_VERSION = 1
TERMINAL_RUN_STATES = {"promoted", "aborted", "failed"}
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
    "processed_tokens",
    "cost_usd",
    "assistant_messages",
    "tool_calls",
    "tool_results",
)


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _counter(values: Iterable[str | None]) -> dict[str, int]:
    return dict(sorted(Counter(value for value in values if value).items()))


def aggregate_usage(
    usages: Iterable[dict[str, Any] | None],
    *,
    scope: str,
) -> dict[str, Any]:
    """Sum normalized usage while preserving per-field coverage evidence."""
    records = [usage for usage in usages if isinstance(usage, dict)]
    result: dict[str, Any] = {"scope": scope, "sources_total": len(records)}
    coverage: dict[str, int] = {}
    for field in USAGE_FIELDS:
        values = [_number(record.get(field)) for record in records]
        present = [value for value in values if value is not None]
        result[field] = sum(present) if present else None
        coverage[field] = len(present)
    result["coverage"] = coverage
    result["incomplete_fields"] = [
        field for field, count in coverage.items() if count < len(records)
    ]
    result["complete"] = bool(records) and all(
        coverage[field] == len(records)
        for field in ("processed_tokens", "cost_usd")
    )
    return result


def _reaches_target(
    score: float | None,
    target: float | None,
    direction: str,
) -> bool | None:
    if score is None or target is None:
        return None
    return score >= target if direction == "maximize" else score <= target


def _favorable_improvement(
    score: float | None,
    baseline: float | None,
    direction: str,
) -> float | None:
    if score is None or baseline is None:
        return None
    return score - baseline if direction == "maximize" else baseline - score


def _iteration_elapsed(iteration: IterationRecord) -> float:
    total = 0.0
    for metrics in iteration.metrics.values():
        if isinstance(metrics, dict):
            elapsed = _number(metrics.get("elapsed_seconds"))
            if elapsed is not None:
                total += float(elapsed)
    return total


def _verifier_results_elapsed(results: Iterable[Any]) -> float:
    total = 0.0
    for result in results:
        metrics = result.metrics if isinstance(result.metrics, dict) else {}
        elapsed = _number(metrics.get("elapsed_seconds"))
        if elapsed is not None:
            total += float(elapsed)
    return total


def _lineage_statistics(candidates: list[CandidateRecord]) -> dict[str, Any]:
    parents_by_candidate: dict[str, set[str]] = {}
    for candidate in candidates:
        task = candidate.task
        parents = set(task.parent_candidate_ids)
        if task.parent_id:
            parents.add(task.parent_id)
        if task.base_candidate_id:
            parents.add(task.base_candidate_id)
        parents.discard(candidate.candidate_id)
        parents_by_candidate[candidate.candidate_id] = parents

    def depth(candidate_id: str, path: set[str]) -> int | None:
        if candidate_id in path:
            return None
        parents = parents_by_candidate.get(candidate_id, set())
        if not parents:
            return 0
        parent_depths = [depth(parent, path | {candidate_id}) for parent in parents]
        known = [value for value in parent_depths if value is not None]
        return 1 + max(known) if known else None

    depths = [depth(candidate.candidate_id, set()) for candidate in candidates]
    known_depths = [value for value in depths if value is not None]
    return {
        "root_candidates": sum(not parents for parents in parents_by_candidate.values()),
        "derived_candidates": sum(bool(parents) for parents in parents_by_candidate.values()),
        "multi_parent_candidates": sum(
            len(parents) > 1 for parents in parents_by_candidate.values()
        ),
        "lineage_edges": sum(len(parents) for parents in parents_by_candidate.values()),
        "max_depth": max(known_depths) if known_depths else None,
        "cycles_detected": sum(value is None for value in depths),
    }


def _first_elapsed(
    iterations: list[IterationRecord],
    *,
    run_created_epoch: float | None,
    predicate: Callable[[IterationRecord], bool],
) -> float | None:
    if run_created_epoch is None:
        return None
    epochs = [
        epoch
        for iteration in iterations
        if predicate(iteration) and (epoch := _epoch(iteration.created_at)) is not None
    ]
    return max(0.0, min(epochs) - run_created_epoch) if epochs else None


def build_run_statistics(
    run: RunRecord,
    frozen: FrozenSpec,
    candidates: list[CandidateRecord],
    sessions: list[AgentSessionRecord],
    observability_by_session: dict[str, dict[str, Any]],
    *,
    baseline_score: float | None = None,
    target_score: float | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Build a stable, read-only statistical view from durable run evidence."""
    now_epoch = time.time() if now_epoch is None else now_epoch
    direction = frozen.spec.metric_direction
    iterations = [
        iteration for candidate in candidates for iteration in candidate.iterations
    ]
    observations = [
        observability_by_session.get(session.agent_session_id, {}) for session in sessions
    ]
    usage = aggregate_usage(
        [observation.get("usage") for observation in observations],
        scope="worker_sessions",
    )

    session_iterations: dict[str, list[IterationRecord]] = {
        session.agent_session_id: [] for session in sessions
    }
    for iteration in iterations:
        if iteration.agent_session_id in session_iterations:
            session_iterations[iteration.agent_session_id].append(iteration)

    terminal_states: list[str | None] = []
    models: list[str | None] = []
    providers: list[str | None] = []
    durations: list[float] = []
    ended_epochs: list[float] = []
    timed_out = 0
    runner_failed = 0
    for session, observation in zip(sessions, observations, strict=True):
        execution = observation.get("execution")
        execution = execution if isinstance(execution, dict) else {}
        terminal_states.append(
            str(execution["terminal_state"])
            if execution.get("terminal_state")
            else None
        )
        models.append(str(execution["model"]) if execution.get("model") else None)
        providers.append(
            str(execution["provider"]) if execution.get("provider") else None
        )
        duration = _number(execution.get("duration_seconds"))
        if duration is not None:
            durations.append(float(duration))
        ended = _epoch(execution.get("ended_at"))
        if ended is not None:
            ended_epochs.append(ended)
        timed_out += int(bool(execution.get("timed_out")))
        runner_failed += int(bool(execution.get("runner_failed")))

    productive_sessions = sum(bool(values) for values in session_iterations.values())
    successful_sessions = sum(
        any(
            iteration.process_passed is True
            and _reaches_target(iteration.score, target_score, direction) is not False
            for iteration in values
        )
        for values in session_iterations.values()
    )
    successful_candidates = sum(
        any(
            iteration.process_passed is True
            and _reaches_target(iteration.score, target_score, direction) is not False
            for iteration in candidate.iterations
        )
        for candidate in candidates
    )

    process_passed = sum(iteration.process_passed is True for iteration in iterations)
    process_failed = sum(iteration.process_passed is False for iteration in iterations)
    process_unknown = len(iterations) - process_passed - process_failed
    promotion_reports = [
        candidate.promotion_report
        for candidate in candidates
        if candidate.promotion_report is not None
    ]
    promotion_passed = sum(report.promotion_passed is True for report in promotion_reports)
    promotion_failed = sum(report.promotion_passed is False for report in promotion_reports)

    process_verifier_elapsed = sum(
        _iteration_elapsed(iteration) for iteration in iterations
    )
    promotion_verifier_elapsed = sum(
        _verifier_results_elapsed(report.verifier_results)
        for report in promotion_reports
    )
    failure_classes = [iteration.failure_class for iteration in iterations]
    for report in promotion_reports:
        failure_classes.extend(result.failure_class for result in report.verifier_results)

    created_epoch = _epoch(run.created_at)
    evidence_epochs = [
        epoch
        for iteration in iterations
        if (epoch := _epoch(iteration.created_at)) is not None
    ]
    evidence_epochs.extend(ended_epochs)
    evidence_epochs.extend(
        epoch
        for candidate in candidates
        if candidate.promotion_evidence is not None
        and (epoch := _epoch(candidate.promotion_evidence.created_at)) is not None
    )
    run_age = (
        max(0.0, now_epoch - created_epoch) if created_epoch is not None else None
    )
    terminal = str(run.state) in TERMINAL_RUN_STATES
    observed_end = max(evidence_epochs) if evidence_epochs else None
    observed_duration = (
        max(0.0, observed_end - created_epoch)
        if terminal and created_epoch is not None and observed_end is not None
        else run_age
    )

    selected_iteration: IterationRecord | None = None
    for candidate in candidates:
        if candidate.candidate_id != run.selected_candidate_id:
            continue
        selected_iteration = next(
            (
                iteration
                for iteration in candidate.iterations
                if iteration.iteration == run.selected_iteration
            ),
            None,
        )
        break

    target_reached = _reaches_target(run.best_score, target_score, direction)
    time_to_first_verifier = _first_elapsed(
        iterations,
        run_created_epoch=created_epoch,
        predicate=lambda _iteration: True,
    )
    time_to_first_passing_verifier = _first_elapsed(
        iterations,
        run_created_epoch=created_epoch,
        predicate=lambda iteration: iteration.process_passed is True,
    )
    time_to_first_improvement = (
        _first_elapsed(
            iterations,
            run_created_epoch=created_epoch,
            predicate=lambda iteration: iteration.process_passed is True
            and (
                _favorable_improvement(iteration.score, baseline_score, direction) or 0
            )
            > 0,
        )
        if baseline_score is not None
        else None
    )
    time_to_threshold = (
        _first_elapsed(
            iterations,
            run_created_epoch=created_epoch,
            predicate=lambda iteration: iteration.process_passed is True
            and _reaches_target(iteration.score, target_score, direction) is True,
        )
        if target_score is not None
        else None
    )
    selected_epoch = _epoch(selected_iteration.created_at) if selected_iteration else None
    time_to_selected_score = (
        max(0.0, selected_epoch - created_epoch)
        if selected_epoch is not None and created_epoch is not None
        else None
    )

    cost = _number(usage.get("cost_usd"))
    processed_tokens = _number(usage.get("processed_tokens"))
    selected_candidate = next(
        (
            candidate
            for candidate in candidates
            if candidate.candidate_id == run.selected_candidate_id
        ),
        None,
    )
    parent_verified = bool(
        selected_candidate
        and any(
            iteration.agent_session_id is None
            and iteration.process_passed is True
            and (
                run.selected_artifact_hash is None
                or iteration.artifact_hash == run.selected_artifact_hash
            )
            for iteration in selected_candidate.iterations
        )
    )
    promotion_required = bool(frozen.spec.promotion_verifiers)
    promotion_verified = bool(
        selected_candidate
        and selected_candidate.promotion_report is not None
        and selected_candidate.promotion_report.promotion_passed is True
    )
    selection_survived = bool(
        run.selected_candidate_id
        and parent_verified
        and (promotion_verified or not promotion_required)
    )
    missing: list[str] = []
    if baseline_score is None:
        missing.append("baseline_score")
    if target_score is None:
        missing.append("target_score")
    if usage["coverage"]["processed_tokens"] < len(sessions):
        missing.append("worker_processed_tokens")
    if usage["coverage"]["cost_usd"] < len(sessions):
        missing.append("worker_cost_usd")
    if len(durations) < len(sessions):
        missing.append("worker_duration")
    if any(
        _number(
            (observation.get("execution") or {}).get("time_to_first_token_ms")
            if isinstance(observation.get("execution"), dict)
            else None
        )
        is None
        for observation in observations
    ):
        missing.append("worker_time_to_first_token")

    return {
        "schema_version": STATISTICS_SCHEMA_VERSION,
        "run": {
            "state": str(run.state),
            "terminal": terminal,
            "age_seconds": run_age,
            "observed_duration_seconds": observed_duration,
            "terminal_timestamp_source": (
                "latest_durable_evidence" if terminal and observed_end is not None else None
            ),
        },
        "scores": {
            "metric_name": frozen.spec.metric_name,
            "direction": direction,
            "baseline": baseline_score,
            "target": target_score,
            "best": run.best_score,
            "selected": run.selected_score,
            "best_improvement_from_baseline": _favorable_improvement(
                run.best_score, baseline_score, direction
            ),
            "selected_improvement_from_baseline": _favorable_improvement(
                run.selected_score, baseline_score, direction
            ),
            "target_reached": target_reached,
            "successful_candidates": successful_candidates,
            "candidate_success_rate": _rate(successful_candidates, len(candidates)),
        },
        "timing": {
            "time_to_first_verifier_seconds": time_to_first_verifier,
            "time_to_first_passing_verifier_seconds": time_to_first_passing_verifier,
            "time_to_first_improvement_seconds": time_to_first_improvement,
            "time_to_threshold_seconds": time_to_threshold,
            "time_to_first_success_seconds": (
                time_to_threshold
                if target_score is not None
                else time_to_first_passing_verifier
            ),
            "time_to_selected_score_seconds": time_to_selected_score,
            "worker_duration_seconds_total": sum(durations) if durations else None,
            "worker_duration_sessions_observed": len(durations),
            "process_verifier_elapsed_seconds_total": process_verifier_elapsed,
            "promotion_verifier_elapsed_seconds_observed": promotion_verifier_elapsed,
            "verifier_elapsed_seconds_total": (
                process_verifier_elapsed + promotion_verifier_elapsed
            ),
        },
        "workers": {
            "sessions_total": len(sessions),
            "productive_sessions": productive_sessions,
            "productive_session_rate": _rate(productive_sessions, len(sessions)),
            "successful_sessions": successful_sessions,
            "successful_session_rate": _rate(successful_sessions, len(sessions)),
            "timed_out": timed_out,
            "runner_failed": runner_failed,
            "hosts": _counter(session.host for session in sessions),
            "providers": _counter(providers),
            "models": _counter(models),
            "terminal_states": _counter(terminal_states),
        },
        "lineage": _lineage_statistics(candidates),
        "selection": {
            "selected": run.selected_candidate_id is not None,
            "parent_verified": parent_verified,
            "promotion_required": promotion_required,
            "promotion_verified": promotion_verified,
            "survived": selection_survived,
        },
        "verifiers": {
            "process_runs": len(iterations),
            "worker_process_runs": sum(
                iteration.agent_session_id is not None for iteration in iterations
            ),
            "parent_process_runs": sum(
                iteration.agent_session_id is None for iteration in iterations
            ),
            "process_verifier_results": sum(
                len(iteration.metrics) for iteration in iterations
            ),
            "process_passed": process_passed,
            "process_failed": process_failed,
            "process_unknown": process_unknown,
            "process_pass_rate": _rate(process_passed, len(iterations)),
            "promotion_reports_observed": len(promotion_reports),
            "promotion_passed": promotion_passed,
            "promotion_failed": promotion_failed,
            "failure_classes": _counter(failure_classes),
        },
        "usage": usage,
        "efficiency": {
            "known_cost_per_process_run_usd": (
                float(cost) / len(iterations) if cost is not None and iterations else None
            ),
            "known_cost_per_successful_candidate_usd": (
                float(cost) / successful_candidates
                if cost is not None and successful_candidates
                else None
            ),
            "known_cost_per_score_improvement_usd": (
                float(cost)
                / improvement
                if cost is not None
                and (
                    improvement := _favorable_improvement(
                        run.best_score, baseline_score, direction
                    )
                )
                not in (None, 0)
                else None
            ),
            "processed_tokens_per_process_run": (
                float(processed_tokens) / len(iterations)
                if processed_tokens is not None and iterations
                else None
            ),
        },
        "data_quality": {
            "missing": missing,
            "promotion_attempt_history_available": False,
            "terminal_time_is_inferred": terminal,
        },
    }


def aggregate_run_statistics(statistics: Iterable[dict[str, Any] | None]) -> dict[str, Any]:
    records = [record for record in statistics if isinstance(record, dict)]
    workers = [record.get("workers", {}) for record in records]
    verifiers = [record.get("verifiers", {}) for record in records]
    scores = [record.get("scores", {}) for record in records]
    selection = [record.get("selection", {}) for record in records]
    targets_known = sum(score.get("target") is not None for score in scores)
    targets_reached = sum(score.get("target_reached") is True for score in scores)
    selections_total = sum(item.get("selected") is True for item in selection)
    selections_survived = sum(item.get("survived") is True for item in selection)
    return {
        "runs_total": len(records),
        "targets_known": targets_known,
        "targets_reached": targets_reached,
        "run_success_rate": _rate(targets_reached, targets_known),
        "selections_total": selections_total,
        "selections_survived": selections_survived,
        "selection_survival_rate": _rate(selections_survived, selections_total),
        "workers": {
            key: sum(int(worker.get(key) or 0) for worker in workers)
            for key in (
                "sessions_total",
                "productive_sessions",
                "successful_sessions",
                "timed_out",
                "runner_failed",
            )
        },
        "verifiers": {
            key: sum(int(verifier.get(key) or 0) for verifier in verifiers)
            for key in (
                "process_runs",
                "worker_process_runs",
                "parent_process_runs",
                "process_verifier_results",
                "process_passed",
                "process_failed",
                "promotion_reports_observed",
                "promotion_passed",
                "promotion_failed",
            )
        },
        "usage": aggregate_usage(
            [record.get("usage") for record in records],
            scope="search_tasks",
        ),
    }
