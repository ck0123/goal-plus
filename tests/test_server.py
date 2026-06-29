from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import Mock

from fastmcp import FastMCP

import agentic_any_search_mcp.server as server_module
from agentic_any_search_mcp.server import create_mcp


def test_create_mcp(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")
    assert isinstance(mcp, FastMCP)


def test_create_mcp_registers_expected_tools(tmp_path: Path) -> None:
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
        "search_get_agent_context",
        "search_update_agent_status",
        "search_list_agent_status",
        "search_finish_agent_session",
        "search_abort_agent_session",
        "search_abort_all_agent_sessions",
        "search_publish_observation",
        "search_list_observations",
        "search_wait_agent_events",
        "search_submit_candidate",
        "search_run_verifier",
        "search_select",
        "search_report",
        "search_promote",
    }


def test_start_agent_session_accepts_string_directive_and_budget(tmp_path: Path) -> None:
    mcp = create_mcp(tmp_path / ".search")

    tools = asyncio.run(mcp.get_tools())
    schema = tools["search_start_agent_session"].parameters
    directive = schema["properties"]["directive"]
    budget = schema["properties"]["budget"]

    assert {"type": "string"} in directive["anyOf"]
    assert {"type": "object", "additionalProperties": True} in budget["anyOf"]


def test_create_mcp_constructs_runtime_with_configured_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    created_roots = []

    class FakeRuntime:
        def __init__(self, root_dir):
            created_roots.append(root_dir)

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

        def search_get_agent_context(self, *args, **kwargs):
            return {}

        def search_update_agent_status(self, *args, **kwargs):
            return {}

        def search_list_agent_status(self, *args, **kwargs):
            return []

        def search_finish_agent_session(self, *args, **kwargs):
            return {}

        def search_abort_agent_session(self, *args, **kwargs):
            return {}

        def search_abort_all_agent_sessions(self, *args, **kwargs):
            return {}

        def search_publish_observation(self, *args, **kwargs):
            return {}

        def search_list_observations(self, *args, **kwargs):
            return []

        def search_wait_agent_events(self, *args, **kwargs):
            return {}

        def search_submit_candidate(self, *args, **kwargs):
            return {}

        def search_run_verifier(self, *args, **kwargs):
            return {}

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
    assert created_roots == [tmp_path / "custom-search"]
