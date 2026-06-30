from __future__ import annotations

import asyncio
from pathlib import Path

from fastmcp import FastMCP

import agentic_any_search_mcp.server as server_module
from agentic_any_search_mcp.server import create_mcp


def test_create_mcp(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")
    assert isinstance(mcp, FastMCP)


def test_create_mcp_registers_only_opencode_native_tools(tmp_path: Path) -> None:
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
        "search_bind_opencode_session",
        "search_continue_agent_session",
        "search_get_agent_context",
        "search_run_verifier",
        "search_list_iterations",
        "search_select",
        "search_report",
        "search_promote",
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


def test_create_mcp_constructs_runtime_with_configured_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    created_runtimes = []

    class FakeRuntime:
        def __init__(self, root_dir):
            created_runtimes.append(root_dir)

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

    monkeypatch.setattr(server_module, "FileSearchRuntime", FakeRuntime)
    monkeypatch.setattr(server_module, "SearchTools", FakeTools)

    mcp = create_mcp(tmp_path / "custom-search")

    assert isinstance(mcp, FastMCP)
    assert created_runtimes == [tmp_path / "custom-search"]
