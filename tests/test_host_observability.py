from __future__ import annotations

import json
from pathlib import Path

import pytest

from goal_plus.host_observability import (
    collect_codex_observability,
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
    assert result["usage"]["cached_input_tokens"] == 600
    assert result["usage"]["assistant_messages"] == 1
    assert result["usage"]["tool_calls"] == 1
    assert result["usage"]["tool_results"] == 1
    assert result["context"]["percent"] == 28.0
    assert result["artifacts"]["session_file"] == str(session_path)
    assert "prompt" not in json.dumps(result)
    assert "not returned" not in json.dumps(result)


def test_pi_observability_normalizes_legacy_metrics(tmp_path: Path) -> None:
    session = _session(
        tmp_path,
        host="pi-rpc",
        handle=AgentHostHandle(
            host="pi-rpc",
            metadata={
                "pi_metrics": {
                    "scope": "run_delta",
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
    assert result["execution"]["model"] == "gpt-5.5"
    assert result["execution"]["reasoning_effort"] == "medium"
    assert result["execution"]["terminal_state"] == "completed"
    assert result["usage"]["total_tokens"] == 125
    assert result["usage"]["cost_usd"] == 0.1
    assert result["context"]["tokens"] == 12000
