from __future__ import annotations

import asyncio
from pathlib import Path

from fastmcp import FastMCP

import goal_plus.server as server_module
from goal_plus.server import create_mcp


def test_create_mcp(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")
    assert isinstance(mcp, FastMCP)


def test_create_mcp_registers_search_runtime_tools(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")

    tools = asyncio.run(mcp.get_tools())

    assert set(tools) == {
        "search_freeze_spec",
        "search_create",
        "search_status",
        "search_list_history",
        "search_plan_next",
        "search_start_batch",
        "search_start_agent_session",
        "search_redispatch_candidate",
        "search_bind_agent_handle",
        "search_bind_opencode_session",
        "search_continue_agent_session",
        "search_get_agent_context",
        "search_run_verifier",
        "search_list_iterations",
        "search_select",
        "search_report",
        "search_promote",
        "goal_plus_create",
        "goal_plus_status",
        "goal_plus_update_goal",
        "goal_plus_monitor_snapshot",
        "goal_plus_record_triage",
        "goal_plus_save_spec_draft",
        "goal_plus_confirm_frozen_verifier",
        "goal_plus_link_search_run",
        "goal_plus_record_search_result",
        "goal_plus_prepare_final_check",
        "goal_plus_submit_final_check",
        "goal_plus_set_status",
        "goal_plus_gate",
    }


def test_start_agent_session_returns_launch_payload(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")

    tools = asyncio.run(mcp.get_tools())
    schema = tools["search_start_agent_session"].parameters

    properties = schema["properties"]
    assert "candidate_id" in properties
    assert "directive" in properties
    # The legacy admission parameters (budget, visibility_mode) are gone.
    assert "budget" not in properties
    assert "visibility_mode" not in properties


def test_redispatch_candidate_exposes_worker_overrides(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")

    tools = asyncio.run(mcp.get_tools())
    schema = tools["search_redispatch_candidate"].parameters

    properties = schema["properties"]
    assert "run_id" in properties
    assert "candidate_id" in properties
    assert "directive" in properties
    assert "worker_agent_type" in properties
    assert "worker_budget" in properties


def test_continue_agent_session_exposes_task_id_launch_payload(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")

    tools = asyncio.run(mcp.get_tools())

    assert "agent_session_id" in tools["search_bind_opencode_session"].parameters["properties"]
    assert "opencode_session_id" in tools["search_bind_opencode_session"].parameters["properties"]
    assert "agent_session_id" in tools["search_continue_agent_session"].parameters["properties"]
    assert "directive" in tools["search_continue_agent_session"].parameters["properties"]


def test_run_verifier_exposes_optional_agent_session_id(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")

    tools = asyncio.run(mcp.get_tools())
    schema = tools["search_run_verifier"].parameters

    assert "agent_session_id" in schema["properties"]


def test_goal_plus_gate_exposes_hook_friendly_schema(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")

    tools = asyncio.run(mcp.get_tools())
    schema = tools["goal_plus_gate"].parameters

    assert "goal_plus_id" in schema["properties"]
    assert "event" in schema["properties"]
    assert "context" in schema["properties"]


def test_goal_plus_monitor_snapshot_exposes_read_only_schema(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")

    tools = asyncio.run(mcp.get_tools())
    schema = tools["goal_plus_monitor_snapshot"].parameters

    assert "goal_plus_id" in schema["properties"]
    assert "run_id" in schema["properties"]
    assert "stale_after_seconds" in schema["properties"]


def test_goal_plus_create_has_no_mode_hint_and_confirm_tool_is_registered(
    tmp_path: Path,
) -> None:
    mcp = create_mcp(tmp_path / ".search")

    tools = asyncio.run(mcp.get_tools())
    create_schema = tools["goal_plus_create"].parameters
    confirm_schema = tools["goal_plus_confirm_frozen_verifier"].parameters

    assert "mode_hint" not in create_schema["properties"]
    assert "raw_goal" in create_schema["properties"]
    assert "goal_plus_id" in confirm_schema["properties"]
    assert "confirmed_by" in confirm_schema["properties"]
    assert "evidence" in confirm_schema["properties"]
    update_schema = tools["goal_plus_update_goal"].parameters
    prepare_schema = tools["goal_plus_prepare_final_check"].parameters
    submit_schema = tools["goal_plus_submit_final_check"].parameters
    assert set(update_schema["required"]) >= {
        "goal_plus_id",
        "raw_goal",
        "expected_revision",
    }
    assert "checker_host" in prepare_schema["properties"]
    assert set(submit_schema["required"]) >= {
        "goal_plus_id",
        "check_id",
        "goal_revision",
        "verdict",
        "summary",
    }
    assert submit_schema["properties"]["verdict"]["enum"] == [
        "pass",
        "fail",
        "interrupted",
    ]


def test_create_mcp_constructs_runtime_with_configured_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    created_runtimes = []
    created_goal_runtimes = []

    class FakeRuntime:
        def __init__(self, root_dir):
            created_runtimes.append(root_dir)

    class FakeGoalRuntime:
        def __init__(self, root_dir):
            created_goal_runtimes.append(root_dir)

    class FakeTools:
        def __init__(self, runtime):
            self.runtime = runtime

        def search_freeze_spec(self, *args, **kwargs):
            return {}

        def search_create(self, *args, **kwargs):
            return {}

        def search_status(self, *args, **kwargs):
            return {}

        def search_list_history(self, *args, **kwargs):
            return {}

        def search_plan_next(self, *args, **kwargs):
            return {}

        def search_start_batch(self, *args, **kwargs):
            return []

        def search_start_agent_session(self, *args, **kwargs):
            return {}

        def search_bind_agent_handle(self, *args, **kwargs):
            return {}

        def search_bind_opencode_session(self, *args, **kwargs):
            return {}

        def search_continue_agent_session(self, *args, **kwargs):
            return {}

        def search_get_agent_context(self, *args, **kwargs):
            return {}

        def search_run_verifier(self, *args, **kwargs):
            return {}

        def search_list_iterations(self, *args, **kwargs):
            return []

        def search_select(self, *args, **kwargs):
            return {}

        def search_report(self, *args, **kwargs):
            return {}

        def search_promote(self, *args, **kwargs):
            return {}

    class FakeGoalTools:
        def __init__(self, runtime):
            self.runtime = runtime

        def goal_plus_create(self, *args, **kwargs):
            return {}

        def goal_plus_status(self, *args, **kwargs):
            return {}

        def goal_plus_record_triage(self, *args, **kwargs):
            return {}

        def goal_plus_save_spec_draft(self, *args, **kwargs):
            return {}

        def goal_plus_confirm_frozen_verifier(self, *args, **kwargs):
            return {}

        def goal_plus_link_search_run(self, *args, **kwargs):
            return {}

        def goal_plus_record_search_result(self, *args, **kwargs):
            return {}

        def goal_plus_set_status(self, *args, **kwargs):
            return {}

        def goal_plus_gate(self, *args, **kwargs):
            return {}

    monkeypatch.setattr(server_module, "FileSearchRuntime", FakeRuntime)
    monkeypatch.setattr(server_module, "FileGoalPlusRuntime", FakeGoalRuntime)
    monkeypatch.setattr(server_module, "SearchTools", FakeTools)
    monkeypatch.setattr(server_module, "GoalPlusTools", FakeGoalTools)

    mcp = create_mcp(tmp_path / "custom-search")

    assert isinstance(mcp, FastMCP)
    assert created_runtimes == [tmp_path / "custom-search"]
    assert created_goal_runtimes == [tmp_path / "custom-search"]
