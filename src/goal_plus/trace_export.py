from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import DEFAULT_RUNTIME_ROOT
from .runtime import load_json, parse_utc_timestamp, write_json


TRACE_PID = 1
RUNTIME_TID = 1
PLAN_TID = 10
SUBAGENT_TID_BASE = 100
VERIFIER_TID_BASE = 200
ARTIFACT_TID = 300
DEFAULT_OPENCODE_LOG_PATH = Path.home() / ".local/share/opencode/log/opencode.log"


def build_chrome_trace(
    root_dir: str | Path,
    run_id: str,
    opencode_log_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a Chrome Trace Event JSON payload for one search run."""
    root = Path(root_dir)
    run_dir = root / "runs" / run_id
    run = load_json(run_dir / "run.json")
    frozen = load_json(root / "specs" / run["frozen_spec_id"] / "frozen_spec.json")
    plans = _load_json_files(run_dir / "plans")
    candidates = _load_json_files(run_dir / "candidates", "candidate.json")
    sessions = _load_json_files(run_dir / "agent_sessions")
    opencode_traces = _load_opencode_traces(sessions, opencode_log_path)

    base_seconds = _base_seconds(run, plans, candidates, sessions, run_dir, opencode_traces)
    events: list[dict[str, Any]] = []
    events.extend(_metadata_events(candidates))
    events.append(_run_event(run, frozen, run_dir, base_seconds))
    events.extend(_plan_events(plans, base_seconds))
    events.extend(_subagent_events(sessions, base_seconds, opencode_traces))
    events.extend(_opencode_events(sessions, base_seconds, opencode_traces))
    events.extend(_verifier_events(candidates, base_seconds))
    events.extend(_artifact_events(run_dir, base_seconds))
    events.sort(key=lambda event: (event.get("ts", -1), event.get("tid", 0), event.get("name", "")))

    return {
        "displayTimeUnit": "ms",
        "traceEvents": events,
    }


def export_chrome_trace(
    root_dir: str | Path,
    run_id: str,
    out_path: str | Path | None = None,
    opencode_log_path: str | Path | None = None,
) -> Path:
    """Write `<run>/trace.json` in Chrome Trace Event format and return its path."""
    root = Path(root_dir)
    output = Path(out_path) if out_path is not None else root / "runs" / run_id / "trace.json"
    write_json(output, build_chrome_trace(root, run_id, opencode_log_path))
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a Search MCP run as Chrome Trace Event JSON.",
    )
    parser.add_argument(
        "--root",
        default=DEFAULT_RUNTIME_ROOT,
        help="Search runtime root directory.",
    )
    parser.add_argument("--run-id", required=True, help="Run id to export.")
    parser.add_argument("--out", default=None, help="Output path. Defaults to <run>/trace.json.")
    parser.add_argument(
        "--opencode-log",
        default=None,
        help=(
            "OpenCode log path. Defaults to "
            "~/.local/share/opencode/log/opencode.log when present."
        ),
    )
    args = parser.parse_args(argv)

    trace_path = export_chrome_trace(
        args.root,
        args.run_id,
        args.out,
        opencode_log_path=args.opencode_log,
    )
    print(trace_path)
    return 0


def _load_json_files(base: Path, leaf_name: str | None = None) -> list[dict[str, Any]]:
    if not base.exists():
        return []
    pattern = f"*/{leaf_name}" if leaf_name else "*.json"
    return [load_json(path) for path in sorted(base.glob(pattern))]


def _base_seconds(
    run: dict[str, Any],
    plans: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    run_dir: Path,
    opencode_traces: dict[str, dict[str, Any]] | None = None,
) -> float:
    timestamps: list[float] = []
    for value in _timestamp_values(run, plans, candidates, sessions):
        parsed = _parse_timestamp(value)
        if parsed is not None:
            timestamps.append(parsed)
    for trace in (opencode_traces or {}).values():
        timestamps.extend(
            event["seconds"]
            for event in trace.get("events", [])
            if isinstance(event.get("seconds"), int | float)
        )
    for path in [run_dir / "report.md", *(run_dir / "promotion").glob("*.patch")]:
        if path.exists():
            timestamps.append(path.stat().st_mtime)
    return min(timestamps) if timestamps else 0.0


def _timestamp_values(
    run: dict[str, Any],
    plans: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> list[str]:
    values: list[str] = []
    if run.get("created_at"):
        values.append(run["created_at"])
    values.extend(plan["created_at"] for plan in plans if plan.get("created_at"))
    for session in sessions:
        values.extend(
            value
            for value in [session.get("created_at"), session.get("updated_at")]
            if value
        )
    for candidate in candidates:
        values.extend(
            iteration["created_at"]
            for iteration in candidate.get("iterations", [])
            if iteration.get("created_at")
        )
    return values


def _metadata_events(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = [
        _metadata_event(RUNTIME_TID, "MCP runtime"),
        _metadata_event(PLAN_TID, "plans"),
        _metadata_event(ARTIFACT_TID, "artifacts"),
    ]
    for candidate in candidates:
        index = _candidate_index(candidate.get("candidate_id", ""))
        events.append(
            _metadata_event(
                SUBAGENT_TID_BASE + index,
                f"{candidate.get('candidate_id', 'candidate')} subagent",
            )
        )
        events.append(
            _metadata_event(
                VERIFIER_TID_BASE + index,
                f"{candidate.get('candidate_id', 'candidate')} verifier",
            )
        )
    return events


def _metadata_event(tid: int, name: str) -> dict[str, Any]:
    return {
        "name": "thread_name",
        "ph": "M",
        "pid": TRACE_PID,
        "tid": tid,
        "args": {"name": name},
    }


def _run_event(
    run: dict[str, Any],
    frozen: dict[str, Any],
    run_dir: Path,
    base_seconds: float,
) -> dict[str, Any]:
    start = _parse_timestamp(run.get("created_at")) or base_seconds
    end = _run_end_seconds(run_dir, start)
    spec = frozen.get("spec", {})
    return {
        "name": f"run {run['run_id']}",
        "cat": "run",
        "ph": "X",
        "pid": TRACE_PID,
        "tid": RUNTIME_TID,
        "ts": _to_microseconds(start, base_seconds),
        "dur": _duration_microseconds(start, end),
        "args": {
            "run_id": run["run_id"],
            "state": run.get("state"),
            "objective": spec.get("objective"),
            "metric_name": spec.get("metric_name"),
            "metric_direction": spec.get("metric_direction"),
            "strategy": spec.get("strategy", {}),
            "candidates_total": run.get("candidates_total"),
            "candidates_evaluated": run.get("candidates_evaluated"),
            "best_candidate_id": run.get("best_candidate_id"),
            "best_score": run.get("best_score"),
            "selected_candidate_id": run.get("selected_candidate_id"),
        },
    }


def _run_end_seconds(run_dir: Path, fallback: float) -> float:
    paths = [
        run_dir / "report.md",
        run_dir / "run.json",
        *(run_dir / "candidates").glob("*/candidate.json"),
        *(run_dir / "agent_sessions").glob("*.json"),
        *(run_dir / "promotion").glob("*.patch"),
    ]
    mtimes = [path.stat().st_mtime for path in paths if path.exists()]
    return max(mtimes) if mtimes else fallback


def _plan_events(plans: list[dict[str, Any]], base_seconds: float) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for plan in plans:
        start = _parse_timestamp(plan.get("created_at")) or base_seconds
        events.append(
            {
                "name": plan["plan_id"],
                "cat": "plan",
                "ph": "X",
                "pid": TRACE_PID,
                "tid": PLAN_TID,
                "ts": _to_microseconds(start, base_seconds),
                "dur": 1,
                "args": {
                    "run_id": plan.get("run_id"),
                    "strategy": plan.get("strategy", {}).get("name"),
                    "requested_k": plan.get("requested_k"),
                    "planned_k": plan.get("planned_k"),
                    "remaining_budget": plan.get("remaining_budget"),
                    "requires_agent_proposals": plan.get("requires_agent_proposals"),
                    "started_candidate_ids": plan.get("started_candidate_ids", []),
                    "strategy_trace": plan.get("strategy_trace", {}),
                },
            }
        )
    return events


def _subagent_events(
    sessions: list[dict[str, Any]],
    base_seconds: float,
    opencode_traces: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for session in sessions:
        mcp_start = _parse_timestamp(session.get("created_at")) or base_seconds
        mcp_end = _parse_timestamp(session.get("updated_at")) or mcp_start
        candidate_id = session.get("candidate_id", "")
        opencode_trace = opencode_traces.get(str(session.get("opencode_session_id")))
        start, end, timing_source = _subagent_timing(mcp_start, mcp_end, opencode_trace)
        launch = session.get("launch", {})
        subagent_type = launch.get("subagent_type") or "subagent"
        args = {
            "run_id": session.get("run_id"),
            "candidate_id": candidate_id,
            "agent_session_id": session.get("agent_session_id"),
            "opencode_session_id": session.get("opencode_session_id"),
            "description": launch.get("description"),
            "directive": session.get("directive", {}),
            "workspace": session.get("workspace"),
            "counters": session.get("counters", {}),
            "timing_source": timing_source,
            "mcp_created_at": session.get("created_at"),
            "mcp_updated_at": session.get("updated_at"),
        }
        if opencode_trace is not None:
            args.update(_opencode_trace_args(opencode_trace))
        events.append(
            {
                "name": f"{candidate_id} {subagent_type}".strip(),
                "cat": "subagent",
                "ph": "X",
                "pid": TRACE_PID,
                "tid": SUBAGENT_TID_BASE + _candidate_index(candidate_id),
                "ts": _to_microseconds(start, base_seconds),
                "dur": _duration_microseconds(start, end),
                "args": args,
            }
        )
    return events


def _subagent_timing(
    mcp_start: float,
    mcp_end: float,
    opencode_trace: dict[str, Any] | None,
) -> tuple[float, float, str]:
    if opencode_trace is None:
        return mcp_start, mcp_end, "mcp_agent_session"
    opencode_start = opencode_trace.get("created_seconds")
    opencode_end = opencode_trace.get("completed_seconds")
    start = opencode_start if isinstance(opencode_start, int | float) else mcp_start
    end = opencode_end if isinstance(opencode_end, int | float) else mcp_end
    if end < start:
        end = start
    if isinstance(opencode_start, int | float) and isinstance(opencode_end, int | float):
        return start, end, "opencode_log"
    return start, end, "opencode_log_partial"


def _opencode_trace_args(trace: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {
        "opencode_log_path": trace.get("log_path"),
        "opencode_started_at": trace.get("created_at"),
        "opencode_completed_at": trace.get("completed_at"),
        "opencode_steps": trace.get("steps", []),
    }
    if trace.get("created_seconds") is not None and trace.get("completed_seconds") is not None:
        args["opencode_duration_seconds"] = round(
            trace["completed_seconds"] - trace["created_seconds"],
            3,
        )
    metadata = trace.get("metadata", {})
    if isinstance(metadata, dict):
        for source_key, target_key in [
            ("run", "opencode_run"),
            ("title", "opencode_title"),
            ("agent", "opencode_agent"),
            ("model", "opencode_model"),
            ("modelID", "opencode_model_id"),
        ]:
            if metadata.get(source_key) is not None:
                args[target_key] = metadata[source_key]
    return args


def _opencode_events(
    sessions: list[dict[str, Any]],
    base_seconds: float,
    opencode_traces: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for session in sessions:
        opencode_session_id = session.get("opencode_session_id")
        trace = opencode_traces.get(str(opencode_session_id))
        if trace is None:
            continue
        candidate_id = session.get("candidate_id", "")
        tid = SUBAGENT_TID_BASE + _candidate_index(candidate_id)
        for event in trace.get("events", []):
            seconds = event.get("seconds")
            if not isinstance(seconds, int | float):
                continue
            fields = event.get("fields", {})
            args = {
                "source": "opencode_log",
                "opencode_session_id": opencode_session_id,
                "timestamp": event.get("timestamp"),
            }
            if isinstance(fields, dict):
                args.update(fields)
            events.append(
                {
                    "name": _opencode_event_name(candidate_id, event),
                    "cat": "opencode",
                    "ph": "i",
                    "s": "t",
                    "pid": TRACE_PID,
                    "tid": tid,
                    "ts": _to_microseconds(seconds, base_seconds),
                    "args": args,
                }
            )
    return events


def _opencode_event_name(candidate_id: str, event: dict[str, Any]) -> str:
    message = event.get("message") or "event"
    fields = event.get("fields", {})
    if message == "loop" and isinstance(fields, dict) and fields.get("step") is not None:
        return f"{candidate_id} opencode loop {fields['step']}"
    return f"{candidate_id} opencode {message}"


def _verifier_events(
    candidates: list[dict[str, Any]],
    base_seconds: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id", "")
        tid = VERIFIER_TID_BASE + _candidate_index(candidate_id)
        for iteration in candidate.get("iterations", []):
            for verifier_name, metrics in iteration.get("metrics", {}).items():
                elapsed = _metric_elapsed_seconds(metrics)
                end = _parse_timestamp(iteration.get("created_at")) or base_seconds
                start = max(base_seconds, end - elapsed)
                events.append(
                    {
                        "name": f"{verifier_name} {candidate_id}#{iteration.get('iteration')}",
                        "cat": "verifier",
                        "ph": "X",
                        "pid": TRACE_PID,
                        "tid": tid,
                        "ts": _to_microseconds(start, base_seconds),
                        "dur": max(1, int(elapsed * 1_000_000)),
                        "args": {
                            "candidate_id": candidate_id,
                            "iteration": iteration.get("iteration"),
                            "agent_session_id": iteration.get("agent_session_id"),
                            "score": iteration.get("score"),
                            "failure_class": iteration.get("failure_class"),
                            "changed_files": iteration.get("changed_files", []),
                            "metrics": metrics,
                        },
                    }
                )
    return events


def _artifact_events(run_dir: Path, base_seconds: float) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    report_path = run_dir / "report.md"
    if report_path.exists():
        events.append(_instant_event("report.md", "artifact", report_path, base_seconds))
    for patch_path in sorted((run_dir / "promotion").glob("*.patch")):
        events.append(_instant_event(patch_path.name, "artifact", patch_path, base_seconds))
    return events


def _instant_event(name: str, category: str, path: Path, base_seconds: float) -> dict[str, Any]:
    return {
        "name": name,
        "cat": category,
        "ph": "i",
        "s": "g",
        "pid": TRACE_PID,
        "tid": ARTIFACT_TID,
        "ts": _to_microseconds(path.stat().st_mtime, base_seconds),
        "args": {"path": str(path)},
    }


def _load_opencode_traces(
    sessions: list[dict[str, Any]],
    opencode_log_path: str | Path | None,
) -> dict[str, dict[str, Any]]:
    session_ids = {
        str(session["opencode_session_id"])
        for session in sessions
        if session.get("opencode_session_id")
    }
    log_path = _resolve_opencode_log_path(opencode_log_path)
    if not session_ids or log_path is None:
        return {}

    traces: dict[str, dict[str, Any]] = {
        session_id: {
            "opencode_session_id": session_id,
            "log_path": str(log_path),
            "events": [],
            "steps": [],
            "metadata": {},
        }
        for session_id in session_ids
    }
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            event = _parse_opencode_log_line(line, session_ids)
            if event is None:
                continue
            trace = traces[event["opencode_session_id"]]
            trace["events"].append(event)
            message = event.get("message")
            fields = event.get("fields", {})
            if message == "created":
                trace.setdefault("created_at", event["timestamp"])
                trace.setdefault("created_seconds", event["seconds"])
                trace["metadata"] = fields
            elif message == "exiting loop":
                trace["completed_at"] = event["timestamp"]
                trace["completed_seconds"] = event["seconds"]
            elif message == "loop" and fields.get("step") is not None:
                trace["steps"].append(fields["step"])
    return traces


def _resolve_opencode_log_path(opencode_log_path: str | Path | None) -> Path | None:
    path = Path(opencode_log_path).expanduser() if opencode_log_path else DEFAULT_OPENCODE_LOG_PATH
    return path if path.exists() else None


def _parse_opencode_log_line(
    line: str,
    session_ids: set[str],
) -> dict[str, Any] | None:
    timestamp = _log_field(line, "timestamp")
    seconds = _parse_timestamp(timestamp)
    if timestamp is None or seconds is None:
        return None

    message = _log_field(line, "message")
    opencode_session_id = _log_field(line, "id") if message == "created" else None
    opencode_session_id = opencode_session_id or _log_field(line, "session.id")
    if opencode_session_id not in session_ids:
        return None

    fields = _opencode_fields(line)
    fields["message"] = message
    return {
        "opencode_session_id": opencode_session_id,
        "timestamp": timestamp,
        "seconds": seconds,
        "message": message,
        "fields": fields,
    }


def _opencode_fields(line: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in [
        "run",
        "level",
        "message",
        "id",
        "session.id",
        "title",
        "agent",
        "model",
        "modelID",
        "providerID",
        "mode",
        "step",
        "messageID",
        "time.created",
        "time.updated",
    ]:
        value = _log_field(line, key)
        if value is not None:
            fields[key] = _coerce_opencode_field(key, value)
    return fields


def _coerce_opencode_field(key: str, value: str) -> str | int:
    if key == "step":
        try:
            return int(value)
        except ValueError:
            return value
    return value


def _log_field(line: str, key: str) -> str | None:
    match = re.search(
        rf"(?:^|\s){re.escape(key)}=(?:\"((?:\\\"|[^\"])*)\"|([^\s]+))",
        line,
    )
    if match is None:
        return None
    value = match.group(1) if match.group(1) is not None else match.group(2)
    return value.replace('\\"', '"')


def _parse_timestamp(timestamp: Any) -> float | None:
    if not isinstance(timestamp, str):
        return None
    try:
        return parse_utc_timestamp(timestamp)
    except (TypeError, ValueError):
        pass
    try:
        normalized = timestamp.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _to_microseconds(seconds: float, base_seconds: float) -> int:
    return max(0, int(round((seconds - base_seconds) * 1_000_000)))


def _duration_microseconds(start: float, end: float) -> int:
    return max(1, int(round((end - start) * 1_000_000)))


def _metric_elapsed_seconds(metrics: Any) -> float:
    if not isinstance(metrics, dict):
        return 0.0
    elapsed = metrics.get("elapsed_seconds")
    if isinstance(elapsed, int | float):
        return max(0.0, float(elapsed))
    return 0.0


def _candidate_index(candidate_id: str) -> int:
    match = re.search(r"(\d+)$", candidate_id)
    return int(match.group(1)) if match else 0


if __name__ == "__main__":
    raise SystemExit(main())
