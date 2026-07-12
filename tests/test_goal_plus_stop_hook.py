from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from goal_plus.goal_plus import FileGoalPlusRuntime


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "hooks" / "goal_plus_stop.py"
HOOK_CLI = [
    sys.executable,
    "-m",
    "goal_plus.server",
    "--goal-plus-host-hook",
]


def _run_hook(tmp_path: Path, search_root: Path, hook_input: dict | None = None, **env):
    run_env = {
        **os.environ,
        "GOAL_PLUS_SEARCH_ROOT": str(search_root),
        "GOAL_PLUS_PROJECT_ROOT": str(tmp_path),
        **{key: str(value) for key, value in env.items()},
    }
    return subprocess.run(
        HOOK_CLI,
        cwd=tmp_path,
        input=json.dumps(hook_input or {}),
        text=True,
        capture_output=True,
        check=False,
        env=run_env,
    )


def test_legacy_stop_hook_script_still_runs(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"

    result = subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=tmp_path,
        input="{}",
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "GOAL_PLUS_SEARCH_ROOT": str(search_root),
            "GOAL_PLUS_PROJECT_ROOT": str(tmp_path),
        },
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_stop_hook_allows_when_no_goal_state_and_does_not_create_state(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"

    result = _run_hook(tmp_path, search_root)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert not search_root.exists()


def test_stop_hook_allows_unbound_active_goal_without_session_match(
    tmp_path: Path,
) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")

    result = _run_hook(tmp_path, search_root, {"stop_reason": "done"})

    assert result.returncode == 0
    assert result.stdout == ""

    events = runtime.list_events(record.goal_plus_id)
    assert events[-1]["event_type"] == "session_gate_skipped"
    assert events[-1]["payload"]["reason"] == "no_matching_session"


def test_stop_hook_allows_goal_mode_without_required_next_action(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Tidy docs wording")
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
            "reasons": ["qualitative docs task"],
        },
    )

    runtime.activate_session(
        record.goal_plus_id,
        {
            "host": "codex",
            "session_id": "session-current",
            "transcript_path": "/tmp/current.jsonl",
        },
    )

    result = _run_hook(
        tmp_path,
        search_root,
        {"hook_event_name": "Stop", "session_id": "session-current"},
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert runtime.list_events(record.goal_plus_id)[-1]["event_type"] == "gate_allowed"


def test_stop_hook_can_target_explicit_goal_id(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    first = runtime.create_goal("Optimize kernel")
    second = runtime.create_goal("Tidy docs")
    runtime.record_triage(
        second.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
            "reasons": ["qualitative docs task"],
        },
    )

    result = _run_hook(tmp_path, search_root, GOAL_PLUS_ID=first.goal_plus_id)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert runtime.list_events(first.goal_plus_id)[-1]["event_type"] == "gate_blocked"


def test_post_tool_use_goal_plus_create_binds_main_session(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")

    result = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "session-main",
            "transcript_path": "/tmp/main.jsonl",
            "tool_name": "mcp__goal-plus__goal_plus_create",
            "tool_response": {"goal_plus_id": record.goal_plus_id},
        },
    )

    assert result.returncode == 0
    assert result.stdout == ""
    updated = runtime.status(record.goal_plus_id)
    assert updated.active_session is not None
    assert updated.active_session.host == "codex"
    assert updated.active_session.session_id == "session-main"
    assert updated.active_session.transcript_path == "/tmp/main.jsonl"
    assert updated.active_session.state == "attached"


def test_post_tool_use_goal_plus_create_ignores_subagent_context(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")

    result = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "session-main",
            "agent_id": "agent-sub",
            "agent_type": "search-candidate-agent",
            "agent_transcript_path": "/tmp/subagent.jsonl",
            "tool_name": "mcp__goal-plus__goal_plus_create",
            "tool_response": {"goal_plus_id": record.goal_plus_id},
        },
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert runtime.status(record.goal_plus_id).active_session is None


def test_stop_hook_blocks_only_current_bound_session(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")
    runtime.activate_session(
        record.goal_plus_id,
        {
            "host": "claude-code",
            "session_id": "session-a",
            "transcript_path": "/tmp/session-a.jsonl",
        },
    )

    interrupted = _run_hook(
        tmp_path,
        search_root,
        {"hook_event_name": "Stop", "session_id": "session-b"},
    )

    assert interrupted.returncode == 0
    assert interrupted.stdout == ""
    events = runtime.list_events(record.goal_plus_id)
    assert events[-1]["event_type"] == "session_gate_skipped"
    assert events[-1]["payload"]["current_session_id"] == "session-b"

    same_session = _run_hook(
        tmp_path,
        search_root,
        {"hook_event_name": "Stop", "session_id": "session-a"},
    )

    assert same_session.returncode == 0
    payload = json.loads(same_session.stdout)
    assert payload["decision"] == "block"
    assert "Classify whether the raw goal" in payload["reason"]


def test_stop_hook_disable_env_allows_without_gate_event(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")
    before = runtime.list_events(record.goal_plus_id)

    result = _run_hook(
        tmp_path,
        search_root,
        GOAL_PLUS_STOP_HOOK_DISABLED="1",
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert runtime.list_events(record.goal_plus_id) == before
