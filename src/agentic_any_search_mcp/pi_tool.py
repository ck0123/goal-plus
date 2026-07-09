from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from agentic_any_search_mcp.goal_plus import FileGoalPlusRuntime
from agentic_any_search_mcp.pi_driver import run_pi_search_candidate
from agentic_any_search_mcp.runtime import FileSearchRuntime
from agentic_any_search_mcp.tools import GoalPlusTools, SearchTools


SEARCH_TOOL_NAMES = {
    "search_freeze_spec",
    "search_create",
    "search_status",
    "search_list_history",
    "search_plan_next",
    "search_start_batch",
    "search_start_agent_session",
    "search_redispatch_candidate",
    "search_bind_opencode_session",
    "search_bind_agent_handle",
    "search_continue_agent_session",
    "search_get_agent_context",
    "search_run_verifier",
    "search_list_iterations",
    "search_select",
    "search_report",
    "search_promote",
}

GOAL_PLUS_TOOL_NAMES = {
    "goal_plus_create",
    "goal_plus_status",
    "goal_plus_record_triage",
    "goal_plus_save_spec_draft",
    "goal_plus_confirm_frozen_verifier",
    "goal_plus_link_search_run",
    "goal_plus_record_search_result",
    "goal_plus_set_status",
    "goal_plus_gate",
}


def _pi_search_run_candidate_tool(
    root_dir: Path | str,
) -> Callable[..., dict[str, Any]]:
    def call(
        run_id: str,
        candidate_id: str,
        directive: dict[str, Any] | str | None = None,
        final_verify: bool = True,
        pi_binary: str = "pi",
        extension_path: str | None = None,
        thinking_level: str | None = None,
        model_pattern: str | None = None,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        return run_pi_search_candidate(
            root_dir=root_dir,
            run_id=run_id,
            candidate_id=candidate_id,
            directive=directive,
            final_verify=final_verify,
            pi_binary=pi_binary,
            extension_path=extension_path,
            thinking_level=thinking_level,
            model_pattern=model_pattern,
            provider=provider,
            model_id=model_id,
        )

    return call


def _registry(root_dir: Path | str) -> dict[str, Callable[..., Any]]:
    search_tools = SearchTools(FileSearchRuntime(root_dir))
    goal_tools = GoalPlusTools(FileGoalPlusRuntime(root_dir))
    tools: dict[str, Callable[..., Any]] = {}
    for name in SEARCH_TOOL_NAMES:
        tools[name] = getattr(search_tools, name)
    for name in GOAL_PLUS_TOOL_NAMES:
        tools[name] = getattr(goal_tools, name)
    tools["pi_search_run_candidate"] = _pi_search_run_candidate_tool(root_dir)
    tools["goal_plus_monitor_snapshot"] = search_tools.goal_plus_monitor_snapshot
    return tools


def call_pi_tool(
    root_dir: Path | str,
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> Any:
    tools = _registry(root_dir)
    if tool_name not in tools:
        raise ValueError(f"unsupported pi tool: {tool_name}")
    return tools[tool_name](**(args or {}))


def _read_args(args_json: str | None) -> dict[str, Any]:
    raw = args_json
    if raw is None:
        raw = sys.stdin.read().strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must be a JSON object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="JSON CLI facade for Pi search-runtime extension tools."
    )
    parser.add_argument("tool", help="Tool name, e.g. search_get_agent_context")
    parser.add_argument(
        "--root",
        default=os.environ.get("AGENTIC_ANY_SEARCH_ROOT", ".search"),
        help="Search runtime storage directory",
    )
    parser.add_argument(
        "--args-json",
        help="JSON object of tool arguments. Defaults to stdin.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parsed = parser.parse_args(argv)

    try:
        result = call_pi_tool(parsed.root, parsed.tool, _read_args(parsed.args_json))
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc), "tool": parsed.tool},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if parsed.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
