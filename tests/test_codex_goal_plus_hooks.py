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
    assert "do not resume merely because Goal Plus is active" in context
    assert "scope, deliverables, or success criteria" in context
    assert "goal_plus_update_goal" in context
    assert "clarify ambiguous intent before resuming" in context


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


def test_with_final_check_precreates_required_policy(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"

    result = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-codex",
            "prompt": "/goal-plus-with-final-check Ship the complete feature",
        },
    )

    context = _additional_context(result, "UserPromptSubmit")
    record_path = next((search_root / "goal-plus").glob("gp_*/goal.json"))
    record = FileGoalPlusRuntime(search_root).status(record_path.parent.name)
    assert record.policy["final_check"]["mode"] == "required"
    assert record.goal_revision == 1
    assert "required" in context
    assert "Load and follow the goal-plus skill" in context


def test_interrupted_session_restores_then_edits_same_goal(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"
    initial = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-codex",
            "prompt": "/goal-plus Implement requirement A",
        },
    )
    initial_context = _additional_context(initial, "UserPromptSubmit")
    record_path = next((search_root / "goal-plus").glob("gp_*/goal.json"))
    goal_id = record_path.parent.name

    restored = _run_hook(
        tmp_path,
        search_root,
        {"hook_event_name": "SessionStart", "session_id": "session-codex"},
    )
    assert goal_id in _additional_context(restored, "SessionStart")

    edited = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-codex",
            "prompt": "/goal-plus edit Implement requirements A and B",
        },
    )
    edited_context = _additional_context(edited, "UserPromptSubmit")
    records = list((search_root / "goal-plus").glob("gp_*/goal.json"))
    assert len(records) == 1
    record = FileGoalPlusRuntime(search_root).status(goal_id)
    assert record.goal_revision == 2
    assert record.raw_goal == "Implement requirements A and B"
    assert [revision.raw_goal for revision in record.goal_revisions] == [
        "Implement requirement A",
        "Implement requirements A and B",
    ]
    assert "revision: 2" in edited_context
    assert initial_context != edited_context


def test_interrupted_session_explicit_resume_keeps_same_revision(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"
    _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-codex",
            "prompt": "$goal-plus Implement requirement A",
        },
    )
    record_path = next((search_root / "goal-plus").glob("gp_*/goal.json"))
    goal_id = record_path.parent.name

    resumed = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-codex",
            "prompt": "$goal-plus resume",
        },
    )
    context = _additional_context(resumed, "UserPromptSubmit")
    record = FileGoalPlusRuntime(search_root).status(goal_id)
    assert record.goal_revision == 1
    assert record.raw_goal == "Implement requirement A"
    assert goal_id in context


def test_codex_interrupted_final_checker_is_recorded_and_can_stop(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal(
        "Ship it",
        policy={"final_check": {"mode": "required"}},
    )
    runtime.activate_session(
        record.goal_plus_id,
        {"host": "codex", "session_id": "session-codex"},
    )
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    runtime.prepare_final_check(record.goal_plus_id, "codex")

    result = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "SubagentStop",
            "session_id": "session-codex",
            "agent_id": "checker-1",
            "agent_type": "goal_plus_final_checker",
            "task_name": "goal_plus_final_check_r1",
        },
    )
    assert result.stdout == ""
    interrupted = runtime.status(record.goal_plus_id)
    assert interrupted.final_checks[-1].status == "interrupted"
    assert interrupted.next_action.kind == "retry_final_check"  # type: ignore[union-attr]


def test_codex_stop_blocks_goal_mode_until_required_check_passes(tmp_path: Path) -> None:
    search_root = tmp_path / ".gp"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal(
        "Ship it",
        policy={"final_check": {"mode": "required"}},
    )
    runtime.activate_session(
        record.goal_plus_id,
        {"host": "codex", "session_id": "session-codex"},
    )
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )

    result = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "Stop",
            "session_id": "session-codex",
        },
    )
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "goal_plus_prepare_final_check" in payload["reason"]


def test_interrupted_codex_final_checker_must_submit_before_subagent_stop(
    tmp_path: Path,
) -> None:
    search_root = tmp_path / ".gp"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal(
        "Ship it",
        policy={"final_check": {"mode": "required"}},
    )
    runtime.activate_session(
        record.goal_plus_id,
        {"host": "codex", "session_id": "session-codex"},
    )
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    request = runtime.prepare_final_check(record.goal_plus_id, "codex")

    interrupted = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "SubagentStop",
            "session_id": "session-codex",
            "agent_id": "reviewer",
        },
    )
    assert json.loads(interrupted.stdout)["decision"] == "block"
    assert request["check"]["check_id"] in json.loads(interrupted.stdout)["reason"]

    runtime.submit_final_check(
        record.goal_plus_id,
        check_id=request["check"]["check_id"],
        goal_revision=1,
        verdict="fail",
        summary="Missing evidence",
        findings=[{"requirement": "tests"}],
        evidence=[{"kind": "inspection", "result": "missing"}],
    )
    released = _run_hook(
        tmp_path,
        search_root,
        {
            "hook_event_name": "SubagentStop",
            "session_id": "session-codex",
            "agent_id": "reviewer",
        },
    )
    assert released.stdout == ""


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
