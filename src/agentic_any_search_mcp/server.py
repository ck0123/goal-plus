from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from agentic_any_search_mcp.runtime import FileSearchRuntime
from agentic_any_search_mcp.tools import SearchTools


def create_mcp(root_dir: str | Path = ".search") -> FastMCP:
    runtime = FileSearchRuntime(root_dir)
    tools = SearchTools(runtime)
    mcp = FastMCP("agentic-any-search")

    @mcp.tool()
    def search_freeze_spec(spec: dict[str, Any], verifier_artifact_paths: list[str]) -> dict[str, Any]:
        return tools.search_freeze_spec(spec, verifier_artifact_paths)

    @mcp.tool()
    def search_create(frozen_spec_id: str) -> dict[str, str]:
        return tools.search_create(frozen_spec_id)

    @mcp.tool()
    def search_status(run_id: str) -> dict[str, Any]:
        return tools.search_status(run_id)

    @mcp.tool()
    def search_list_history(
        run_id: str,
        top_n: int = 5,
        sort_by: str = "score",
    ) -> dict[str, Any]:
        return tools.search_list_history(run_id, top_n, sort_by)

    @mcp.tool()
    def search_plan_next(run_id: str, requested_k: int = 4) -> dict[str, Any]:
        return tools.search_plan_next(run_id, requested_k)

    @mcp.tool()
    def search_start_batch(
        run_id: str,
        plan_id: str,
        proposals: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return tools.search_start_batch(run_id, plan_id, proposals)

    @mcp.tool()
    def search_start_agent_session(
        run_id: str,
        candidate_id: str | None = None,
        directive: dict[str, Any] | str | None = None,
        budget: dict[str, Any] | None = None,
        visibility_mode: str = "observations",
    ) -> dict[str, Any]:
        """Register an agent session record for a candidate. Does NOT start a worker.

        This only creates the MCP-side session ledger entry (deadline, heartbeat
        counter, candidate binding). Without a matching `Task(subagent_type=<worker_agent_type>, ...)`
        call from the host, no worker process runs, the session stays idle, and
        `search_wait_agent_events` will block until `worker_timeout_seconds`
        elapses with no actual work done.

        Immediately after this returns `agent_session_id`, the host must launch
        the worker via Task in the same model turn:
          Task(subagent_type="<worker_agent_type>",
               prompt=f"agent_session_id={agent_session_id}; <one-paragraph idea>")

        The Task prompt must contain only `agent_session_id` and a human-readable
        candidate idea — never hard-code `run_id`, `candidate_id`, or workspace
        paths; the worker derives those from `search_get_agent_context`.
        """
        return tools.search_start_agent_session(
            run_id,
            candidate_id,
            directive,
            budget,
            visibility_mode,
        )

    @mcp.tool()
    def search_get_agent_context(agent_session_id: str) -> dict[str, Any]:
        return tools.search_get_agent_context(agent_session_id)

    @mcp.tool()
    def search_update_agent_status(
        agent_session_id: str,
        phase: str,
        current_goal: str = "",
        last_action: str = "",
        next_step: str = "",
        blockers: list[str] | None = None,
        status: str | None = None,
        heartbeat: bool = True,
    ) -> dict[str, Any]:
        return tools.search_update_agent_status(
            agent_session_id,
            phase,
            current_goal,
            last_action,
            next_step,
            blockers,
            status,
            heartbeat,
        )

    @mcp.tool()
    def search_list_agent_status(
        run_id: str,
        include_stale: bool = True,
    ) -> list[dict[str, Any]]:
        return tools.search_list_agent_status(run_id, include_stale)

    @mcp.tool()
    def search_finish_agent_session(
        agent_session_id: str,
        status: str = "completed",
        summary: str = "",
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return tools.search_finish_agent_session(agent_session_id, status, summary, result)

    @mcp.tool()
    def search_request_agent_finalize(agent_session_id: str, reason: str = "") -> dict[str, Any]:
        return tools.search_request_agent_finalize(agent_session_id, reason)

    @mcp.tool()
    def search_abort_agent_session(agent_session_id: str, reason: str = "") -> dict[str, Any]:
        return tools.search_abort_agent_session(agent_session_id, reason)

    @mcp.tool()
    def search_abort_all_agent_sessions(run_id: str, reason: str = "") -> dict[str, Any]:
        return tools.search_abort_all_agent_sessions(run_id, reason)

    @mcp.tool()
    def search_publish_observation(
        agent_session_id: str,
        summary: str,
        evidence: str = "",
        next_ideas: list[str] | None = None,
        tags: list[str] | None = None,
        visibility: str = "observations",
    ) -> dict[str, Any]:
        return tools.search_publish_observation(
            agent_session_id,
            summary,
            evidence,
            next_ideas,
            tags,
            visibility,
        )

    @mcp.tool()
    def search_list_observations(
        run_id: str,
        visibility: str | None = None,
        tags: list[str] | None = None,
        top_n: int = 20,
    ) -> list[dict[str, Any]]:
        return tools.search_list_observations(run_id, visibility, tags, top_n)

    @mcp.tool()
    def search_wait_agent_events(
        run_id: str,
        timeout_seconds: int = 300,
        wake_on: list[str] | None = None,
        since_event_id: str | None = None,
    ) -> dict[str, Any]:
        """Block until a terminal agent event arrives or the poll window expires.

        Returns terminal events (agent_completed/failed/blocked/aborted/timed_out,
        run_deadline) plus the current active session count. Precondition: every
        session you want to supervise must already have a running worker — i.e.
        `search_start_agent_session` followed by a matching host-side
        `Task(subagent_type=..., background=true, ...)` call. Calling wait
        without launching Tasks first produces only `agent_timed_out` events
        after the full `worker_timeout_seconds` window, with no real work done.
        """
        return tools.search_wait_agent_events(run_id, timeout_seconds, wake_on, since_event_id)

    @mcp.tool()
    def search_submit_candidate(
        run_id: str,
        candidate_id: str,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        return tools.search_submit_candidate(run_id, candidate_id, artifact)

    @mcp.tool()
    def search_run_verifier(run_id: str, candidate_id: str, scope: str = "process") -> dict[str, Any]:
        return tools.search_run_verifier(run_id, candidate_id, scope)

    @mcp.tool()
    def search_select(run_id: str, strategy: str = "independent_branches") -> dict[str, Any]:
        return tools.search_select(run_id, strategy)

    @mcp.tool()
    def search_report(run_id: str) -> dict[str, str]:
        return tools.search_report(run_id)

    @mcp.tool()
    def search_promote(run_id: str, candidate_id: str) -> dict[str, str]:
        return tools.search_promote(run_id, candidate_id)

    @mcp.tool()
    def search_abort(run_id: str, reason: str = "") -> dict[str, bool]:
        return tools.search_abort(run_id, reason)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".search", help="Search runtime storage directory")
    args = parser.parse_args()
    create_mcp(args.root).run(transport="stdio")


if __name__ == "__main__":
    main()
