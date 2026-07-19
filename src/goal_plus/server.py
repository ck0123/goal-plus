from __future__ import annotations

import argparse
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from goal_plus.goal_plus import FileGoalPlusRuntime
from goal_plus.models import GoalPlusSpecDraftInput, SearchSpec
from goal_plus.paths import DEFAULT_RUNTIME_ROOT
from goal_plus.runtime import FileSearchRuntime
from goal_plus.tools import GoalPlusTools, SearchTools


def create_mcp(
    root_dir: str | Path = DEFAULT_RUNTIME_ROOT,
) -> FastMCP:
    from fastmcp import FastMCP

    runtime = FileSearchRuntime(root_dir)
    goal_runtime = FileGoalPlusRuntime(root_dir)
    tools = SearchTools(runtime)
    goal_tools = GoalPlusTools(goal_runtime)
    mcp = FastMCP("goal-plus")

    @mcp.tool()
    def search_freeze_spec(
        spec: SearchSpec,
        verifier_artifact_paths: list[str],
    ) -> dict[str, Any]:
        """Freeze a SearchSpec and its verifier files into an immutable bundle.

        Returns `frozen_spec_id`. Call before `search_create`. Verifier files
        are hash-pinned; modifying them during candidate execution forces the
        score to 0.0. Freeze preflights every `ranking_signal`: it must exit 0
        and print a final JSON object containing a finite numeric
        `spec.metric_name`, for example `{"score": 123.0}`. The verifier
        command runs in a disposable source copy and must not change that
        workspace. Use the unique per-invocation directory exposed as
        `GOAL_PLUS_VERIFIER_TMPDIR`, `TMPDIR`, `TMP`, and `TEMP` for compiler
        products and temporary outputs; fixed `/tmp` paths are unsafe when
        candidates verify concurrently. Optional custom verifier files must be
        materialized during Spec Discovery in a source-owned path, never `.gp`
        or `.search`. `expected_outputs`
        contains artifact path/glob strings only; it is not a stdout parser
        configuration. `spec.budget.max_candidates` is the immutable total
        candidate cap across the whole run and all rounds;
        `spec.budget.max_parallel` is the per-batch planning cap. Equal values
        normally permit only one full batch.
        """
        return tools.search_freeze_spec(spec, verifier_artifact_paths)

    @mcp.tool()
    def search_create(
        frozen_spec_id: str,
        source_run_id: str | None = None,
    ) -> dict[str, str]:
        """Start a search run from a frozen spec. Returns `run_id`.

        Pass `source_run_id` only when a new immutable run is unavoidable. The
        new run receives a bounded snapshot of the source frontier, scoped
        pitfalls, and feature ledger. Source scores are historical and must be
        re-verified under the new contract.
        """
        return tools.search_create(frozen_spec_id, source_run_id)

    @mcp.tool()
    def search_status(run_id: str) -> dict[str, Any]:
        """Read-only snapshot of run state, budget usage, and best score."""
        return tools.search_status(run_id)

    @mcp.tool()
    def search_invalidate_run(
        run_id: str,
        reason: Literal[
            "verifier_contract_invalid",
            "verifier_coverage_inadequate",
            "verifier_nondeterministic",
            "verifier_target_mismatch",
            "verifier_infrastructure_failure",
        ],
        summary: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fence a run after the main agent confirms verifier inadequacy.

        This atomically blocks new planning, sessions, verifier records,
        selection, and promotion. It does not control host workers: after this
        call, the main agent must interrupt the host pool, wait until every
        worker is terminal, then repair/freeze the verifier and create a new
        run with `source_run_id`.
        """
        return tools.search_invalidate_run(run_id, reason, summary, evidence)

    @mcp.tool()
    def goal_plus_monitor_snapshot(
        goal_plus_id: str | None = None,
        run_id: str | None = None,
        stale_after_seconds: int = 600,
    ) -> dict[str, Any]:
        """Read-only Goal Plus/Search monitoring snapshot for polling agents.

        Returns run, candidate, agent-session, verifier, host-log, and Pi usage
        evidence from durable `.gp` state. It does not start, wait for, or
        interrupt workers.
        """
        return tools.goal_plus_monitor_snapshot(
            goal_plus_id=goal_plus_id,
            run_id=run_id,
            stale_after_seconds=stale_after_seconds,
        )

    @mcp.tool()
    def search_list_history(
        run_id: str,
        top_n: int = 5,
        sort_by: str = "score",
    ) -> dict[str, Any]:
        """Read-only ranked list of evaluated candidates and their scores."""
        return tools.search_list_history(run_id, top_n, sort_by)

    @mcp.tool()
    def search_plan_next(
        run_id: str,
        requested_k: Annotated[
            int,
            Field(
                gt=0,
                description=(
                    "Candidate count requested for this planning round only. "
                    "The runtime plans min(requested_k, remaining total "
                    "candidate budget, budget.max_parallel). The default 4 is "
                    "a batch-size request, not a whole-run budget."
                ),
            ),
        ] = 4,
    ) -> dict[str, Any]:
        """Plan one candidate batch/round from the frozen whole-run budget.

        `requested_k` applies only to this call. The actual `planned_k` is the
        minimum of `requested_k`, the remaining `budget.max_candidates`, and
        `budget.max_parallel`. Returns `plan_id` plus candidate tasks.
        """
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
        worker_budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a context/provenance handle and host-native launch payload.

        It does not start a worker or track lifecycle. The optional
        `worker_budget` overrides only this dispatch without mutating the
        frozen spec. Use the returned `launch` payload
        with the selected host. The prompt-supplied `candidate_id` is a label
        only; the worker must derive authoritative context from
        `search_get_agent_context`.
        """
        return tools.search_start_agent_session(
            run_id, candidate_id, directive, worker_budget
        )

    @mcp.tool()
    def search_redispatch_candidate(
        run_id: str,
        candidate_id: str,
        worker_agent_type: str | None = None,
        worker_budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new worker launch for an existing candidate workspace.

        This is state-level resume, not same-worker continuation. It returns a
        fresh `agent_session_id` and host launch payload for the same
        candidate/workspace. Optional `worker_agent_type` and `worker_budget`
        override only this dispatch; candidate task policy is unchanged.
        """
        return tools.search_redispatch_candidate(
            run_id,
            candidate_id,
            worker_agent_type,
            worker_budget,
        )

    @mcp.tool()
    def search_bind_agent_handle(
        agent_session_id: str,
        handle: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind a runtime agent session to its host worker handle."""
        return tools.search_bind_agent_handle(agent_session_id, handle)

    @mcp.tool()
    def search_get_agent_observability(
        agent_session_id: str,
    ) -> dict[str, Any]:
        """Read normalized host metrics and artifacts for one agent session.

        The schema is shared across hosts. Codex resolves its native session
        JSONL when available; Pi normalizes bound `pi_metrics`. This call is
        read-only and does not wait for, continue, or interrupt the worker.
        Prompt, reasoning, and tool payload contents are never returned.
        """
        return tools.search_get_agent_observability(agent_session_id)

    @mcp.tool()
    def search_continue_agent_session(
        agent_session_id: str,
        worker_budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return host launch fields for continuing a bound worker session.

        Hosts with native continuation reuse the same worker handle. The
        optional worker budget overrides only this continuation dispatch. The
        continuation prompt is neutral: the worker chooses its next action.
        """
        return tools.search_continue_agent_session(
            agent_session_id,
            worker_budget,
        )

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
        hypothesis: str | None = None,
    ) -> dict[str, Any]:
        """Subagent self-score with `agent_session_id`; main final verify without it.

        Subagents pass their own `agent_session_id` and a concise `hypothesis`
        describing the tested design. The runtime records iteration provenance
        and appends exactly one validated row to the inherited
        `workspace/results.tsv`, then commits that runtime-owned ledger.
        The main agent calls this without `agent_session_id`
        after OpenCode Task completion to confirm the final score. A
        `VerifierWorkspaceSideEffect` with
        `candidate_action="stop_and_report"` is a frozen-verifier infrastructure
        failure: workers must not clean verifier outputs or retry it.
        """
        return tools.search_run_verifier(
            run_id,
            candidate_id,
            scope,
            agent_session_id,
            hypothesis,
        )

    @mcp.tool()
    def search_list_iterations(
        run_id: str,
        candidate_id: str,
    ) -> list[dict[str, Any]]:
        """Read-only list of iteration records for a candidate."""
        return tools.search_list_iterations(run_id, candidate_id)

    @mcp.tool()
    def search_select(run_id: str) -> dict[str, Any]:
        """Pick the best evaluated candidate by score. Call after verifying candidates."""
        return tools.search_select(run_id)

    @mcp.tool()
    def search_report(run_id: str) -> dict[str, str]:
        """Generate final Markdown/HTML reports; linked Goal Plus must be terminal."""
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
    def goal_plus_update_goal(
        goal_plus_id: str,
        raw_goal: str,
        expected_revision: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Replace the effective objective in-place and begin a new auditable revision."""
        return goal_tools.goal_plus_update_goal(
            goal_plus_id,
            raw_goal,
            expected_revision,
            reason,
        )

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
        spec_draft: GoalPlusSpecDraftInput,
    ) -> dict[str, Any]:
        """Save the discovered frozen-spec candidate before search_freeze_spec."""
        return goal_tools.goal_plus_save_spec_draft(goal_plus_id, spec_draft)

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
    def goal_plus_prepare_final_check(
        goal_plus_id: str,
        checker_host: Literal["codex", "pi"],
    ) -> dict[str, Any]:
        """Create or resume the required final-check request and return a host launch payload."""
        return goal_tools.goal_plus_prepare_final_check(goal_plus_id, checker_host)

    @mcp.tool()
    def goal_plus_submit_final_check(
        goal_plus_id: str,
        check_id: str,
        goal_revision: int,
        verdict: Literal["pass", "fail", "interrupted"],
        summary: str,
        findings: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        checker_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record an independent final-check verdict for an exact goal revision."""
        return goal_tools.goal_plus_submit_final_check(
            goal_plus_id,
            check_id,
            goal_revision,
            verdict,
            summary,
            findings,
            evidence,
            checker_metadata,
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
    parser.add_argument(
        "--root",
        default=DEFAULT_RUNTIME_ROOT,
        help="Search runtime storage directory",
    )
    parser.add_argument(
        "--goal-plus-stop-hook",
        action="store_true",
        help="Run the Goal Plus Stop hook instead of starting the MCP server",
    )
    parser.add_argument(
        "--goal-plus-host-hook",
        action="store_true",
        help="Run the Goal Plus host hook instead of starting the MCP server",
    )
    args = parser.parse_args()
    if args.goal_plus_stop_hook or args.goal_plus_host_hook:
        from goal_plus.goal_plus_stop_hook import main as hook_main

        raise SystemExit(hook_main())
    create_mcp(args.root).run(transport="stdio")


if __name__ == "__main__":
    main()
