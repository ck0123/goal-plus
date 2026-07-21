from __future__ import annotations

import json
from pathlib import Path

import pytest

from goal_plus.host_observability import (
    collect_codex_observability,
    collect_codex_transcript_observability,
    collect_pi_observability,
    discover_codex_session_file,
)
from goal_plus.models import AgentHostHandle, AgentSessionRecord


pytestmark = [pytest.mark.codex, pytest.mark.pi]


def _session(
    tmp_path: Path,
    *,
    host: str,
    handle: AgentHostHandle,
    launch: dict[str, object] | None = None,
) -> AgentSessionRecord:
    return AgentSessionRecord(
        agent_session_id="agent_20260716_demo_001",
        run_id="run_20260716_demo",
        candidate_id="c001",
        host=host,  # type: ignore[arg-type]
        host_handle=handle,
        created_at="2026-07-16T10:00:00Z",
        updated_at="2026-07-16T10:00:00Z",
        workspace=tmp_path,
        launch=launch or {},
    )


def test_codex_observability_discovers_and_normalizes_native_session(
    tmp_path: Path,
) -> None:
    task_name = "search_agent_20260716_demo_001"
    session = _session(
        tmp_path,
        host="codex",
        handle=AgentHostHandle(host="codex", task_name=task_name),
    )
    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions" / "2026" / "07" / "16"
    session_dir.mkdir(parents=True)
    session_path = session_dir / "rollout-demo.jsonl"
    events = [
        {
            "timestamp": "2026-07-16T10:00:01Z",
            "type": "session_meta",
            "payload": {
                "id": "codex-thread-1",
                "timestamp": "2026-07-16T10:00:01Z",
                "agent_nickname": "Gibbs",
                "source": {
                    "subagent": {
                        "thread_spawn": {"agent_path": f"/root/{task_name}"}
                    }
                },
            },
        },
        {
            "timestamp": "2026-07-16T10:00:02Z",
            "type": "turn_context",
            "payload": {
                "model": "gpt-5.5",
                "effort": "medium",
                "service_tier": "priority",
            },
        },
        {
            "timestamp": "2026-07-16T10:00:03Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "arguments": "not returned"},
        },
        {
            "timestamp": "2026-07-16T10:00:04Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "output": "not returned"},
        },
        {
            "timestamp": "2026-07-16T10:00:05Z",
            "type": "response_item",
            "payload": {"type": "agent_message", "text": "not returned"},
        },
        {
            "timestamp": "2026-07-16T10:00:06Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 600,
                        "output_tokens": 120,
                        "reasoning_output_tokens": 20,
                        "total_tokens": 1120,
                    },
                    "last_token_usage": {"total_tokens": 280},
                    "model_context_window": 1000,
                },
            },
        },
        {
            "timestamp": "2026-07-16T10:00:07Z",
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "duration_ms": 5000,
                "time_to_first_token_ms": 700,
                "last_agent_message": "not returned",
            },
        },
    ]
    session_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    assert discover_codex_session_file(session, codex_home=codex_home) == session_path
    result = collect_codex_observability(session, codex_home=codex_home)

    assert result["source"] == "codex_session_jsonl"
    assert result["identity"]["native_session_id"] == "codex-thread-1"
    assert result["execution"] == {
        "provider": "openai-codex",
        "model": "gpt-5.5",
        "reasoning_effort": "medium",
        "service_tier": "priority",
        "started_at": "2026-07-16T10:00:01Z",
        "ended_at": "2026-07-16T10:00:07Z",
        "duration_seconds": 5.0,
        "wall_duration_seconds": 6.0,
        "time_to_first_token_ms": 700,
        "turns_completed": 1,
        "terminal_state": "completed",
        "timed_out": False,
        "runner_failed": False,
        "exit_code": None,
    }
    assert result["usage"]["total_tokens"] == 1120
    assert result["usage"]["processed_tokens"] == 1120
    assert result["usage"]["cached_input_tokens"] == 600
    assert result["usage"]["assistant_messages"] == 1
    assert result["usage"]["tool_calls"] == 1
    assert result["usage"]["tool_results"] == 1
    assert result["context"]["percent"] == 28.0
    assert result["artifacts"]["session_file"] == str(session_path)
    assert "prompt" not in json.dumps(result)
    assert "not returned" not in json.dumps(result)


def test_codex_observability_uses_launch_identity_when_transcript_is_missing(
    tmp_path: Path,
) -> None:
    session = _session(
        tmp_path,
        host="codex",
        handle=AgentHostHandle(host="codex", task_name="missing-task"),
        launch={
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
            "service_tier": "priority",
        },
    )

    result = collect_codex_observability(
        session,
        codex_home=tmp_path / "missing-codex-home",
    )

    assert result["source"] == "codex_session_not_found"
    assert result["execution"]["provider"] == "openai-codex"
    assert result["execution"]["model"] == "gpt-5.6-sol"
    assert result["execution"]["reasoning_effort"] == "medium"
    assert result["execution"]["service_tier"] == "priority"
    assert result["execution"]["terminal_state"] == "unknown"
    assert result["execution"]["duration_seconds"] is None
    assert result["usage"]["processed_tokens"] is None


def test_pi_observability_normalizes_legacy_metrics(tmp_path: Path) -> None:
    session = _session(
        tmp_path,
        host="pi-rpc",
        handle=AgentHostHandle(
            host="pi-rpc",
            metadata={
                "pi_metrics": {
                    "scope": "run_delta",
                    "provider": "openai-codex",
                    "model": "gpt-5.5",
                    "thinking_level": "medium",
                    "started_at": "2026-07-16T10:00:00Z",
                    "ended_at": "2026-07-16T10:01:00Z",
                    "duration_seconds": 60.0,
                    "usage_total": {
                        "input": 100,
                        "cacheRead": 50,
                        "cacheWrite": 5,
                        "output": 25,
                        "costTotal": 0.1,
                        "assistantMessages": 2,
                    },
                    "session_stats": {
                        "toolCalls": 7,
                        "toolResults": 6,
                        "contextUsage": {
                            "tokens": 12000,
                            "contextWindow": 240000,
                            "percent": 5.0,
                        }
                    },
                }
            },
        ),
    )

    result = collect_pi_observability(session)

    assert result["source"] == "pi_metrics"
    assert result["execution"]["provider"] == "openai-codex"
    assert result["execution"]["model"] == "gpt-5.5"
    assert result["execution"]["reasoning_effort"] == "medium"
    assert result["execution"]["terminal_state"] == "completed"
    assert result["usage"]["total_tokens"] == 125
    assert result["usage"]["processed_tokens"] == 180
    assert result["usage"]["tool_calls"] == 7
    assert result["usage"]["tool_results"] == 6
    assert result["usage"]["cost_usd"] == 0.1
    assert result["context"]["tokens"] == 12000


def test_codex_transcript_observability_reports_window_delta_without_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rollout.jsonl"
    events = [
        {
            "timestamp": "2026-07-16T09:59:00Z",
            "type": "session_meta",
            "payload": {"id": "thread-1", "timestamp": "2026-07-16T09:59:00Z"},
        },
        {
            "timestamp": "2026-07-16T09:59:01Z",
            "type": "turn_context",
            "payload": {"model": "gpt-5.6", "effort": "high"},
        },
        {
            "timestamp": "2026-07-16T09:59:50Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 5,
                        "total_tokens": 120,
                    },
                    "last_token_usage": {"total_tokens": 30},
                    "model_context_window": 1000,
                },
            },
        },
        {
            "timestamp": "2026-07-16T10:00:01Z",
            "type": "response_item",
            "payload": {"type": "message", "role": "assistant", "content": "secret"},
        },
        {
            "timestamp": "2026-07-16T10:00:02Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "arguments": "secret"},
        },
        {
            "timestamp": "2026-07-16T10:00:03Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 160,
                        "cached_input_tokens": 60,
                        "output_tokens": 30,
                        "reasoning_output_tokens": 8,
                        "total_tokens": 190,
                    },
                    "last_token_usage": {"total_tokens": 50},
                    "model_context_window": 1000,
                },
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    result = collect_codex_transcript_observability(
        path,
        since="2026-07-16T10:00:00Z",
    )

    assert result["usage"]["scope"] == "window_delta"
    assert result["usage"]["input_tokens"] == 60
    assert result["usage"]["cached_input_tokens"] == 20
    assert result["usage"]["output_tokens"] == 10
    assert result["usage"]["total_tokens"] == 70
    assert result["usage"]["processed_tokens"] == 70
    assert result["usage"]["assistant_messages"] == 1
    assert result["usage"]["tool_calls"] == 1
    assert "secret" not in json.dumps(result)
