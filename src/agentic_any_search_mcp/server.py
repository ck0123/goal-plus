from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from agentic_any_search_mcp.goal_plus import FileGoalPlusRuntime
from agentic_any_search_mcp.runtime import FileSearchRuntime
from agentic_any_search_mcp.tools import GoalPlusTools, SearchTools


def create_mcp(
    root_dir: str | Path = ".search",
) -> FastMCP:
    runtime = FileSearchRuntime(root_dir)
    goal_runtime = FileGoalPlusRuntime(root_dir)
    tools = SearchTools(runtime)
    goal_tools = GoalPlusTools(goal_runtime)
    mcp = FastMCP("agentic-any-search")

    @mcp.tool()
    def search_freeze_spec(spec: dict[str, Any], verifier_artifact_paths: list[str]) -> dict[str, Any]:
        """Freeze a SearchSpec and its verifier files into an immutable bundle.

        Returns `frozen_spec_id`. Call before `search_create`. Verifier files
        are hash-pinned; modifying them during candidate execution forces the
        score to 0.0.
        """
        return tools.search_freeze_spec(spec, verifier_artifact_paths)

    @mcp.tool()
    def search_create(frozen_spec_id: str) -> dict[str, str]:
        """Start a search run from a frozen spec. Returns `run_id`."""
        return tools.search_create(frozen_spec_id)

    @mcp.tool()
    def search_status(run_id: str) -> dict[str, Any]:
        """Read-only snapshot of run state, budget usage, and best score."""
        return tools.search_status(run_id)

    @mcp.tool()
    def search_list_history(
        run_id: str,
        top_n: int = 5,
        sort_by: str = "score",
    ) -> dict[str, Any]:
        """Read-only ranked list of evaluated candidates and their scores."""
        return tools.search_list_history(run_id, top_n, sort_by)

    @mcp.tool()
    def search_plan_next(run_id: str, requested_k: int = 4) -> dict[str, Any]:
        """Plan the next batch of candidate workspaces. Returns `plan_id` + candidate tasks."""
        return tools.search_plan_next(run_id, requested_k)

    @mcp.tool()
    def search_start_batch(
        run_id: str,
        plan_id: str,
        proposals: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Materialize planned candidate workspaces (copies of source_path).

        Each returned `CandidateTask` owns an isolated workspace; candidate
        edits must stay inside it. Do not call before `search_plan_next`.
        """
        return tools.search_start_batch(run_id, plan_id, proposals)

    @mcp.tool()
    def search_start_agent_session(
        run_id: str,
        candidate_id: str,
        directive: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Creates a context/provenance handle and returns OpenCode Task launch fields.

        It does not start a worker and does not track lifecycle. The main
        agent must immediately use the returned `launch` payload to spawn an
        OpenCode Task. The prompt-supplied `candidate_id` is a label only;
        the subagent must derive authoritative ids/workspace from
        `search_get_agent_context`.
        """
        return tools.search_start_agent_session(run_id, candidate_id, directive)

    @mcp.tool()
    def search_bind_opencode_session(
        agent_session_id: str,
        opencode_session_id: str,
    ) -> dict[str, Any]:
        """Bind a runtime agent session to the OpenCode Task session id.

        Call this after Task returns using the Task result's
        `metadata.sessionId`. This enables later continuation with
        `search_continue_agent_session`.
        """
        return tools.search_bind_opencode_session(
            agent_session_id,
            opencode_session_id,
        )

    @mcp.tool()
    def search_bind_agent_handle(
        agent_session_id: str,
        handle: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind a runtime agent session to a non-OpenCode host worker handle.

        Used by Codex and Claude Code adapters to record task names, nicknames,
        or agent ids returned by their native foreground worker launch tools.
        OpenCode callers may keep using `search_bind_opencode_session`.
        """
        return tools.search_bind_agent_handle(agent_session_id, handle)

    @mcp.tool()
    def search_continue_agent_session(
        agent_session_id: str,
        directive: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Return launch fields for continuing the same OpenCode subagent session.

        Requires a prior `search_bind_opencode_session`. The returned
        `launch.task_id` must be passed to OpenCode Task as `task_id`, so the
        worker continues the same session, candidate, and workspace instead of
        creating or forking a new one.
        """
        return tools.search_continue_agent_session(agent_session_id, directive)

    @mcp.tool()
    def search_get_agent_context(agent_session_id: str) -> dict[str, Any]:
        """Subagent first call. Authoritative ids and workspace.

        Returns run_id, candidate_id, workspace, candidate_task, history,
        and the subagent's own iterations. Called by the subagent, not the
        main agent. The subagent must treat prompt-supplied ids as labels
        only and rely on this response as the source of truth.
        """
        return tools.search_get_agent_context(agent_session_id)

    @mcp.tool()
    def search_run_verifier(
        run_id: str,
        candidate_id: str,
        scope: str = "process",
        agent_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Subagent self-score with `agent_session_id`; main final verify without it.

        Subagents pass their own `agent_session_id` to record iteration
        provenance. The main agent calls this without `agent_session_id`
        after OpenCode Task completion to confirm the final score.
        """
        return tools.search_run_verifier(run_id, candidate_id, scope, agent_session_id)

    @mcp.tool()
    def search_list_iterations(
        run_id: str,
        candidate_id: str,
    ) -> list[dict[str, Any]]:
        """Read-only list of iteration records for a candidate."""
        return tools.search_list_iterations(run_id, candidate_id)

    @mcp.tool()
    def search_select(run_id: str, strategy: str = "independent_branches") -> dict[str, Any]:
        """Pick the best evaluated candidate by score. Call after verifying candidates."""
        return tools.search_select(run_id, strategy)

    @mcp.tool()
    def search_report(run_id: str) -> dict[str, str]:
        """Generate the run report markdown. Returns the report path."""
        return tools.search_report(run_id)

    @mcp.tool()
    def search_promote(run_id: str, candidate_id: str) -> dict[str, str]:
        """Export the selected candidate as a patch. Does not mutate the main source workspace."""
        return tools.search_promote(run_id, candidate_id)

    @mcp.tool()
    def goal_plus_create(
        raw_goal: str,
        source_path: str | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a goal-plus record from a raw user goal before triage."""
        return goal_tools.goal_plus_create(raw_goal, source_path, policy)

    @mcp.tool()
    def goal_plus_status(goal_plus_id: str) -> dict[str, Any]:
        """Read goal-plus phase, status, linked search state, and evidence log."""
        return goal_tools.goal_plus_status(goal_plus_id)

    @mcp.tool()
    def goal_plus_record_triage(
        goal_plus_id: str,
        triage: dict[str, Any],
    ) -> dict[str, Any]:
        """Record whether a goal should stay goal-like or upgrade toward search."""
        return goal_tools.goal_plus_record_triage(goal_plus_id, triage)

    @mcp.tool()
    def goal_plus_save_spec_draft(
        goal_plus_id: str,
        spec_draft: dict[str, Any],
    ) -> dict[str, Any]:
        """Save the discovered frozen-spec candidate before search_freeze_spec."""
        return goal_tools.goal_plus_save_spec_draft(goal_plus_id, spec_draft)

    @mcp.tool()
    def goal_plus_confirm_frozen_verifier(
        goal_plus_id: str,
        confirmed_by: str = "user",
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record user confirmation for an initially search-ready frozen verifier."""
        return goal_tools.goal_plus_confirm_frozen_verifier(
            goal_plus_id,
            confirmed_by,
            evidence,
        )

    @mcp.tool()
    def goal_plus_link_search_run(
        goal_plus_id: str,
        frozen_spec_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Link an existing Search MCP run to a goal-plus record."""
        return goal_tools.goal_plus_link_search_run(goal_plus_id, frozen_spec_id, run_id)

    @mcp.tool()
    def goal_plus_record_search_result(
        goal_plus_id: str,
        run_id: str,
        selected_candidate_id: str | None = None,
        report_path: str | None = None,
        promotion_artifact_path: str | None = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        """Record selected/promoted search evidence before final raw-goal audit."""
        return goal_tools.goal_plus_record_search_result(
            goal_plus_id,
            run_id,
            selected_candidate_id,
            report_path,
            promotion_artifact_path,
            summary,
        )

    @mcp.tool()
    def goal_plus_set_status(
        goal_plus_id: str,
        status: str,
        reason: str | None = None,
        evidence: list[dict[str, Any]] | None = None,
        next_action: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Set goal-plus status after evidence-based completion, block, or pause."""
        return goal_tools.goal_plus_set_status(
            goal_plus_id,
            status,
            reason,
            evidence,
            next_action,
        )

    @mcp.tool()
    def goal_plus_gate(
        goal_plus_id: str,
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a hook-friendly allow/block decision for goal-plus flow control."""
        return goal_tools.goal_plus_gate(goal_plus_id, event, context)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".search", help="Search runtime storage directory")
    args = parser.parse_args()
    create_mcp(args.root).run(transport="stdio")


if __name__ == "__main__":
    main()
