from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

from goal_plus.models import AgentSessionRecord


OBSERVABILITY_SCHEMA_VERSION = 1


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _timestamp_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _base_observability(session: AgentSessionRecord) -> dict[str, Any]:
    metadata = session.host_handle.metadata
    timed_out = bool(metadata.get("timed_out"))
    runner_failed = bool(metadata.get("runner_failed"))
    terminal_state = _string(metadata.get("terminal_state"))
    if runner_failed:
        terminal_state = "failed"
    elif timed_out:
        terminal_state = "timed_out"
    return {
        "schema_version": OBSERVABILITY_SCHEMA_VERSION,
        "agent_session_id": session.agent_session_id,
        "run_id": session.run_id,
        "candidate_id": session.candidate_id,
        "host": session.host,
        "source": "host_metadata",
        "identity": {
            "native_session_id": None,
            "external_id": session.host_handle.external_id,
            "task_name": session.host_handle.task_name,
            "nickname": session.host_handle.nickname,
        },
        "execution": {
            "model": _string(metadata.get("model")),
            "reasoning_effort": _string(metadata.get("reasoning_effort")),
            "service_tier": _string(metadata.get("service_tier")),
            "started_at": _string(metadata.get("started_at")) or session.created_at,
            "ended_at": _string(metadata.get("ended_at")),
            "duration_seconds": _number(metadata.get("duration_seconds")),
            "wall_duration_seconds": None,
            "time_to_first_token_ms": _number(metadata.get("time_to_first_token_ms")),
            "turns_completed": None,
            "terminal_state": terminal_state or "unknown",
            "timed_out": timed_out,
            "runner_failed": runner_failed,
            "exit_code": _number(metadata.get("exit_code")),
        },
        "usage": {
            "scope": "unavailable",
            "input_tokens": None,
            "cached_input_tokens": None,
            "cache_write_tokens": None,
            "output_tokens": None,
            "reasoning_output_tokens": None,
            "total_tokens": None,
            "cost_usd": None,
            "assistant_messages": None,
            "tool_calls": None,
            "tool_results": None,
        },
        "context": {
            "tokens": None,
            "context_window": None,
            "percent": None,
            "source": "unknown",
        },
        "artifacts": {
            "event_log": _string(metadata.get("event_log")),
            "text_log": _string(metadata.get("text_log")),
            "session_file": _string(metadata.get("session_file")),
        },
        "handoff": {
            "present": isinstance(metadata.get("progress_handoff"), dict),
            "source_path": (
                metadata["progress_handoff"].get("source_path")
                if isinstance(metadata.get("progress_handoff"), dict)
                else None
            ),
            "error": _string(metadata.get("progress_handoff_error")),
        },
        "errors": [],
    }


def collect_metadata_observability(session: AgentSessionRecord) -> dict[str, Any]:
    """Normalize the portable evidence already bound to a host handle."""
    return _base_observability(session)


def _pi_usage(value: Any, *, scope: str) -> dict[str, Any]:
    usage = value if isinstance(value, dict) else {}
    input_tokens = _number(usage.get("input"))
    cached_input_tokens = _number(usage.get("cacheRead"))
    output_tokens = _number(usage.get("output"))
    total_tokens = None
    if input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return {
        "scope": scope,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_tokens": _number(usage.get("cacheWrite")),
        "output_tokens": output_tokens,
        "reasoning_output_tokens": None,
        "total_tokens": total_tokens,
        "cost_usd": _number(usage.get("costTotal")),
        "assistant_messages": _number(usage.get("assistantMessages")),
        "tool_calls": _number(usage.get("toolCalls")),
        "tool_results": _number(usage.get("toolResults")),
    }


def collect_pi_observability(session: AgentSessionRecord) -> dict[str, Any]:
    payload = _base_observability(session)
    metadata = session.host_handle.metadata
    metrics = metadata.get("pi_metrics")
    if not isinstance(metrics, dict):
        return payload

    execution = payload["execution"]
    execution.update(
        {
            "model": _string(metrics.get("model")) or execution["model"],
            "reasoning_effort": (
                _string(metrics.get("thinking_level")) or execution["reasoning_effort"]
            ),
            "started_at": _string(metrics.get("started_at")) or execution["started_at"],
            "ended_at": _string(metrics.get("ended_at")) or execution["ended_at"],
            "duration_seconds": (
                _number(metrics.get("duration_seconds"))
                if _number(metrics.get("duration_seconds")) is not None
                else execution["duration_seconds"]
            ),
        }
    )
    if execution["terminal_state"] == "unknown" and execution["ended_at"]:
        execution["terminal_state"] = "completed"

    scope = _string(metrics.get("scope")) or "session_total"
    payload["usage"] = _pi_usage(metrics.get("usage_total"), scope=scope)
    session_stats = metrics.get("session_stats")
    context_usage = (
        session_stats.get("contextUsage") if isinstance(session_stats, dict) else None
    )
    if isinstance(context_usage, dict):
        payload["context"] = {
            "tokens": _number(context_usage.get("tokens")),
            "context_window": _number(context_usage.get("contextWindow")),
            "percent": _number(context_usage.get("percent")),
            "source": "pi_session_stats",
        }
    elif isinstance(session_stats, dict) and isinstance(session_stats.get("tokens"), dict):
        tokens = session_stats["tokens"]
        payload["context"] = {
            "tokens": _number(tokens.get("total")),
            "context_window": None,
            "percent": None,
            "source": "pi_session_tokens_total",
        }
    payload["source"] = "pi_metrics"
    errors = metrics.get("errors")
    if isinstance(errors, list):
        payload["errors"] = [str(error) for error in errors]
    return payload


def _codex_sessions_root(codex_home: Path | None = None) -> Path:
    if codex_home is not None:
        base = codex_home.expanduser()
    else:
        base = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    return base if base.name == "sessions" else base / "sessions"


def _codex_session_meta(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            for _ in range(8):
                line = stream.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "session_meta" and isinstance(event.get("payload"), dict):
                    return event["payload"]
    except OSError:
        return None
    return None


def _codex_agent_path(meta: dict[str, Any]) -> str | None:
    direct = _string(meta.get("agent_path"))
    if direct:
        return direct
    source = meta.get("source")
    if not isinstance(source, dict):
        return None
    subagent = source.get("subagent")
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    return _string(spawn.get("agent_path")) if isinstance(spawn, dict) else None


def _codex_date_dirs(root: Path, created_at: str) -> list[Path]:
    epoch = _timestamp_epoch(created_at)
    if epoch is None:
        return [root]
    created = datetime.fromtimestamp(epoch, timezone.utc)
    paths = []
    for offset in (-1, 0, 1):
        day = created + timedelta(days=offset)
        candidate = root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        if candidate.exists():
            paths.append(candidate)
    return paths or [root]


def discover_codex_session_file(
    session: AgentSessionRecord,
    *,
    codex_home: Path | None = None,
) -> Path | None:
    """Find a native Codex subagent rollout without mutating runtime state."""
    bound = _string(session.host_handle.metadata.get("session_file"))
    if bound:
        path = Path(bound).expanduser()
        if path.is_file():
            return path

    expected_task = session.host_handle.task_name
    expected_id = session.host_handle.external_id
    if not expected_task and not expected_id:
        return None
    root = _codex_sessions_root(codex_home)
    if not root.exists():
        return None

    best: tuple[float, Path] | None = None
    created_epoch = _timestamp_epoch(session.created_at)
    for directory in _codex_date_dirs(root, session.created_at):
        try:
            candidates = directory.rglob("*.jsonl")
            for path in candidates:
                meta = _codex_session_meta(path)
                if meta is None:
                    continue
                agent_path = _codex_agent_path(meta)
                task_matches = bool(
                    expected_task
                    and agent_path
                    and Path(agent_path).name == Path(expected_task).name
                )
                id_matches = bool(expected_id and meta.get("id") == expected_id)
                if not task_matches and not id_matches:
                    continue
                mtime = path.stat().st_mtime
                distance = abs(mtime - created_epoch) if created_epoch is not None else 0.0
                if best is None or distance < best[0]:
                    best = (distance, path)
        except OSError:
            continue
    return best[1] if best is not None else None


def _codex_usage(info: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    info = info if isinstance(info, dict) else {}
    total = info.get("total_token_usage")
    total = total if isinstance(total, dict) else {}
    last = info.get("last_token_usage")
    last = last if isinstance(last, dict) else {}
    window = _number(info.get("model_context_window"))
    context_tokens = _number(last.get("total_tokens"))
    percent = None
    if context_tokens is not None and window not in (None, 0):
        percent = context_tokens * 100.0 / window
    usage = {
        "scope": "session_total",
        "input_tokens": _number(total.get("input_tokens")),
        "cached_input_tokens": _number(total.get("cached_input_tokens")),
        "cache_write_tokens": None,
        "output_tokens": _number(total.get("output_tokens")),
        "reasoning_output_tokens": _number(total.get("reasoning_output_tokens")),
        "total_tokens": _number(total.get("total_tokens")),
        "cost_usd": None,
        "assistant_messages": None,
        "tool_calls": None,
        "tool_results": None,
    }
    context = {
        "tokens": context_tokens,
        "context_window": window,
        "percent": percent,
        "source": "codex_last_token_usage",
    }
    return usage, context


def _parse_codex_session(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "session_id": None,
        "nickname": None,
        "model": None,
        "reasoning_effort": None,
        "service_tier": None,
        "started_at": None,
        "ended_at": None,
        "duration_seconds": None,
        "wall_duration_seconds": None,
        "time_to_first_token_ms": None,
        "turns_completed": 0,
        "terminal_state": "unknown",
        "usage": None,
        "context": None,
        "assistant_messages": 0,
        "tool_calls": 0,
        "tool_results": 0,
        "errors": [],
    }
    first_epoch: float | None = None
    last_epoch: float | None = None
    active_turns = 0
    terminal_event: str | None = None
    duration_ms = 0.0
    malformed = 0
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as exc:
        result["errors"] = [f"session_file: {type(exc).__name__}: {exc}"]
        return result
    with stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            timestamp = _string(event.get("timestamp"))
            epoch = _timestamp_epoch(timestamp)
            if epoch is not None:
                first_epoch = epoch if first_epoch is None else min(first_epoch, epoch)
                last_epoch = epoch if last_epoch is None else max(last_epoch, epoch)
            event_type = event.get("type")
            payload = event.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            if event_type == "session_meta":
                result["session_id"] = _string(payload.get("id"))
                result["nickname"] = _string(payload.get("agent_nickname"))
                result["started_at"] = _string(payload.get("timestamp")) or timestamp
            elif event_type == "turn_context":
                active_turns += 1
                result["model"] = _string(payload.get("model")) or result["model"]
                result["reasoning_effort"] = (
                    _string(payload.get("effort")) or result["reasoning_effort"]
                )
                result["service_tier"] = (
                    _string(payload.get("service_tier")) or result["service_tier"]
                )
            elif event_type == "event_msg":
                message_type = payload.get("type")
                if message_type == "token_count":
                    usage, context = _codex_usage(payload.get("info"))
                    result["usage"] = usage
                    result["context"] = context
                elif message_type == "task_complete":
                    active_turns = max(0, active_turns - 1)
                    terminal_event = "completed"
                    result["turns_completed"] += 1
                    event_duration = _number(payload.get("duration_ms"))
                    if event_duration is not None:
                        duration_ms += float(event_duration)
                    result["time_to_first_token_ms"] = (
                        _number(payload.get("time_to_first_token_ms"))
                        or result["time_to_first_token_ms"]
                    )
                    result["ended_at"] = timestamp
                elif message_type == "turn_aborted":
                    active_turns = max(0, active_turns - 1)
                    terminal_event = "interrupted"
                    result["ended_at"] = timestamp
            elif event_type == "response_item":
                response_type = payload.get("type")
                if response_type == "agent_message":
                    result["assistant_messages"] += 1
                elif response_type in {"function_call", "custom_tool_call"}:
                    result["tool_calls"] += 1
                elif response_type in {"function_call_output", "custom_tool_call_output"}:
                    result["tool_results"] += 1

    if malformed:
        result["errors"].append(f"ignored {malformed} malformed JSONL line(s)")
    if first_epoch is not None and last_epoch is not None:
        result["wall_duration_seconds"] = max(0.0, last_epoch - first_epoch)
    if duration_ms:
        result["duration_seconds"] = duration_ms / 1000.0
    if active_turns > 0:
        result["terminal_state"] = "running"
        result["ended_at"] = None
    elif terminal_event is not None:
        result["terminal_state"] = terminal_event
    usage = result["usage"]
    if isinstance(usage, dict):
        usage["assistant_messages"] = result["assistant_messages"]
        usage["tool_calls"] = result["tool_calls"]
        usage["tool_results"] = result["tool_results"]
    return result


def collect_codex_observability(
    session: AgentSessionRecord,
    *,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    payload = _base_observability(session)
    session_file = discover_codex_session_file(session, codex_home=codex_home)
    if session_file is None:
        payload["source"] = "codex_session_not_found"
        payload["errors"] = [
            "Codex session JSONL was not bound and could not be discovered from the task name"
        ]
        return payload

    parsed = _parse_codex_session(session_file)
    execution = payload["execution"]
    execution.update(
        {
            "model": parsed["model"] or execution["model"],
            "reasoning_effort": parsed["reasoning_effort"] or execution["reasoning_effort"],
            "service_tier": parsed["service_tier"] or execution["service_tier"],
            "started_at": parsed["started_at"] or execution["started_at"],
            "ended_at": parsed["ended_at"] or execution["ended_at"],
            "duration_seconds": parsed["duration_seconds"],
            "wall_duration_seconds": parsed["wall_duration_seconds"],
            "time_to_first_token_ms": parsed["time_to_first_token_ms"],
            "turns_completed": parsed["turns_completed"],
            "terminal_state": parsed["terminal_state"],
        }
    )
    if execution["runner_failed"]:
        execution["terminal_state"] = "failed"
    elif execution["timed_out"]:
        execution["terminal_state"] = "timed_out"
    if isinstance(parsed["usage"], dict):
        payload["usage"] = parsed["usage"]
    if isinstance(parsed["context"], dict):
        payload["context"] = parsed["context"]
    payload["identity"]["external_id"] = (
        payload["identity"]["external_id"] or parsed["session_id"]
    )
    payload["identity"]["native_session_id"] = parsed["session_id"]
    payload["identity"]["nickname"] = payload["identity"]["nickname"] or parsed["nickname"]
    payload["artifacts"]["session_file"] = str(session_file)
    payload["source"] = "codex_session_jsonl"
    payload["errors"] = parsed["errors"]
    return payload
