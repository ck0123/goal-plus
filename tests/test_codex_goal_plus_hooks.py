from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from goal_plus.goal_plus import FileGoalPlusRuntime


HOOK_CLI = [
    sys.executable,
    "-m",
    "goal_plus.server",
    "--goal-plus-host-hook",
]
pytestmark = pytest.mark.codex


def _run_hook(tmp_path: Path, search_root: Path, hook_input: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        HOOK_CLI,
        cwd=tmp_path,
        input=json.dumps(hook_input),
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "GOAL_PLUS_SEARCH_ROOT": str(search_root),
            "GOAL_PLUS_PROJECT_ROOT": str(tmp_path),
        },
    )


def _additional_context(result: subprocess.CompletedProcess[str], event: str) -> str:
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    specific = payload["hookSpecificOutput"]
    assert specific["hookEventName"] == event
    return specific["additionalContext"]


def test_user_prompt_submit_precreates_and_binds_goal(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"

    result = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-codex",
            "transcript_path": "/tmp/codex.jsonl",
            "prompt": "/goal-plus Optimize model throughput",
        },
    )

    context = _additional_context(result, "UserPromptSubmit")
    runtime = FileGoalPlusRuntime(search_root)
    records = list((search_root / "goal-plus").glob("gp_*/goal.json"))
    assert len(records) == 1
    record = runtime.status(records[0].parent.name)
    assert record.raw_goal == "Optimize model throughput"
    assert record.active_session is not None
    assert record.active_session.host == "codex"
    assert record.active_session.session_id == "session-codex"
    assert record.goal_plus_id in context
    assert "do not call goal_plus_create again" in context


def test_user_prompt_submit_is_idempotent_for_bound_session(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"
    hook_input = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "session-codex",
        "prompt": "$goal-plus Optimize model throughput",
    }

    first = _run_hook(tmp_path, search_root, hook_input)
    second = _run_hook(tmp_path, search_root, hook_input)

    first_context = _additional_context(first, "UserPromptSubmit")
    second_context = _additional_context(second, "UserPromptSubmit")
    records = list((search_root / "goal-plus").glob("gp_*/goal.json"))
    assert len(records) == 1
    assert first_context == second_context


def test_session_start_restores_bound_goal_context(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")
    runtime.activate_session(
        record.goal_plus_id,
        {"host": "codex", "session_id": "session-codex"},
    )

    result = _run_hook(
        tmp_path,
        search_root,
        {"hook_event_name": "SessionStart", "session_id": "session-codex"},
    )

    context = _additional_context(result, "SessionStart")
    assert record.goal_plus_id in context
    assert "record_triage" in context


def test_pre_tool_use_blocks_search_before_spec_is_ready(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")
    runtime.activate_session(
        record.goal_plus_id,
        {"host": "codex", "session_id": "session-codex"},
    )

    result = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-codex",
            "tool_name": "mcp__goal-plus__search_create",
            "tool_input": {},
        },
    )

    payload = json.loads(result.stdout)
    specific = payload["hookSpecificOutput"]
    assert specific == {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "Search tools require a high-confidence frozen spec draft first.",
    }
    assert runtime.status(record.goal_plus_id).hook_counters["pre_tool_use"] == 1


def test_pre_tool_use_ignores_unrelated_tool(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")
    runtime.activate_session(
        record.goal_plus_id,
        {"host": "codex", "session_id": "session-codex"},
    )

    result = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-codex",
            "tool_name": "read_mcp_resource",
        },
    )

    assert result.stdout == ""
    assert runtime.status(record.goal_plus_id).hook_counters == {}


def test_subagent_stop_uses_session_bound_goal_gate(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")
    runtime.activate_session(
        record.goal_plus_id,
        {"host": "codex", "session_id": "session-codex"},
    )

    result = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "SubagentStop",
            "session_id": "session-codex",
            "agent_id": "agent-worker",
            "agent_type": "search_candidate_agent",
        },
    )

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "Classify whether the raw goal" in payload["reason"]
    assert runtime.status(record.goal_plus_id).hook_counters["subagent_stop"] == 1


def test_stop_for_terminal_goal_emits_non_llm_stats(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")
    runtime.activate_session(
        record.goal_plus_id,
        {"host": "codex", "session_id": "session-codex"},
    )
    runtime.set_status(
        record.goal_plus_id,
        status="complete",
        reason="final audit passed",
        evidence=[{"kind": "report", "path": "report.md"}],
    )

    result = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "Stop",
            "session_id": "session-codex",
            "goal_plus_id": record.goal_plus_id,
        },
    )

    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert f"goal_plus_id={record.goal_plus_id}" in message
    assert "status=complete" in message
    assert "phase=intake" in message
    assert "search_tasks=0" in message
    assert "stop=1" in message
