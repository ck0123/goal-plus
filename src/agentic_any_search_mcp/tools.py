from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_any_search_mcp.goal_plus import FileGoalPlusRuntime
from agentic_any_search_mcp.models import (
    CandidateProposal,
    GoalPlusNextAction,
    GoalPlusSpecDraft,
    GoalPlusTriage,
    SearchSpec,
)
from agentic_any_search_mcp.runtime import FileSearchRuntime


class SearchTools:
    """JSON-friendly tool layer shared by tests and the MCP server."""

    def __init__(self, runtime: FileSearchRuntime) -> None:
        self.runtime = runtime

    def search_freeze_spec(self, spec: dict[str, Any], verifier_artifact_paths: list[str]) -> dict[str, Any]:
        frozen = self.runtime.freeze_spec(
            SearchSpec.model_validate(spec),
            [Path(path) for path in verifier_artifact_paths],
        )
        return frozen.model_dump(mode="json")

    def search_create(self, frozen_spec_id: str) -> dict[str, str]:
        return {"run_id": self.runtime.create_run(frozen_spec_id)}

    def search_status(self, run_id: str) -> dict[str, Any]:
        return self.runtime.status(run_id).model_dump(mode="json")

    def search_list_history(
        self,
        run_id: str,
        top_n: int = 5,
        sort_by: str = "score",
    ) -> dict[str, Any]:
        return self.runtime.list_history(run_id, top_n=top_n, sort_by=sort_by)

    def search_plan_next(self, run_id: str, requested_k: int = 4) -> dict[str, Any]:
        return self.runtime.plan_next(run_id, requested_k=requested_k).model_dump(mode="json")

    def search_start_batch(
        self,
        run_id: str,
        plan_id: str,
        proposals: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        parsed_proposals = (
            [CandidateProposal.model_validate(proposal) for proposal in proposals]
            if proposals is not None
            else None
        )
        return [
            task.model_dump(mode="json")
            for task in self.runtime.start_batch(run_id, plan_id, parsed_proposals)
        ]

    def search_start_agent_session(
        self,
        run_id: str,
        candidate_id: str,
        directive: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        return self.runtime.start_agent_session(
            run_id=run_id,
            candidate_id=candidate_id,
            directive=directive,
        ).model_dump(mode="json")

    def search_bind_opencode_session(
        self,
        agent_session_id: str,
        opencode_session_id: str,
    ) -> dict[str, Any]:
        return self.runtime.bind_opencode_session(
            agent_session_id=agent_session_id,
            opencode_session_id=opencode_session_id,
        ).model_dump(mode="json")

    def search_bind_agent_handle(
        self,
        agent_session_id: str,
        handle: dict[str, Any],
    ) -> dict[str, Any]:
        return self.runtime.bind_agent_handle(
            agent_session_id=agent_session_id,
            handle=handle,
        ).model_dump(mode="json")

    def search_continue_agent_session(
        self,
        agent_session_id: str,
        directive: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        return self.runtime.continue_agent_session(
            agent_session_id=agent_session_id,
            directive=directive,
        ).model_dump(mode="json")

    def search_get_agent_context(self, agent_session_id: str) -> dict[str, Any]:
        return self.runtime.get_agent_context(agent_session_id)

    def search_run_verifier(
        self,
        run_id: str,
        candidate_id: str,
        scope: str = "process",
        agent_session_id: str | None = None,
    ) -> dict[str, Any]:
        report = self.runtime.run_verifier(
            run_id,
            candidate_id,
            scope=scope,  # type: ignore[arg-type]
            agent_session_id=agent_session_id,
        )
        return report.model_dump(mode="json")

    def search_list_iterations(
        self,
        run_id: str,
        candidate_id: str,
    ) -> list[dict[str, Any]]:
        return self.runtime.list_iterations(run_id, candidate_id)

    def search_select(self, run_id: str, strategy: str = "independent_branches") -> dict[str, Any]:
        return self.runtime.select(run_id, strategy=strategy)

    def search_report(self, run_id: str) -> dict[str, str]:
        return {"report_path": str(self.runtime.report(run_id))}

    def search_promote(self, run_id: str, candidate_id: str) -> dict[str, str]:
        return {"artifact_path": str(self.runtime.promote(run_id, candidate_id))}


class GoalPlusTools:
    """JSON-friendly goal-plus tool layer shared by tests and the MCP server."""

    def __init__(self, runtime: FileGoalPlusRuntime) -> None:
        self.runtime = runtime

    def goal_plus_create(
        self,
        raw_goal: str,
        source_path: str | None = None,
        mode_hint: str = "auto",
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.runtime.create_goal(
            raw_goal=raw_goal,
            source_path=source_path,
            mode_hint=mode_hint,  # type: ignore[arg-type]
            policy=policy,
        ).model_dump(mode="json")

    def goal_plus_status(self, goal_plus_id: str) -> dict[str, Any]:
        payload = self.runtime.status(goal_plus_id).model_dump(mode="json")
        payload["evidence_log"] = self.runtime.list_events(goal_plus_id)
        return payload

    def goal_plus_record_triage(
        self,
        goal_plus_id: str,
        triage: dict[str, Any],
    ) -> dict[str, Any]:
        return self.runtime.record_triage(
            goal_plus_id,
            GoalPlusTriage.model_validate(triage),
        ).model_dump(mode="json")

    def goal_plus_save_spec_draft(
        self,
        goal_plus_id: str,
        spec_draft: dict[str, Any],
    ) -> dict[str, Any]:
        return self.runtime.save_spec_draft(
            goal_plus_id,
            GoalPlusSpecDraft.model_validate(spec_draft),
        ).model_dump(mode="json")

    def goal_plus_link_search_run(
        self,
        goal_plus_id: str,
        frozen_spec_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        return self.runtime.link_search_run(
            goal_plus_id,
            frozen_spec_id,
            run_id,
        ).model_dump(mode="json")

    def goal_plus_record_search_result(
        self,
        goal_plus_id: str,
        run_id: str,
        selected_candidate_id: str | None = None,
        report_path: str | None = None,
        promotion_artifact_path: str | None = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        return self.runtime.record_search_result(
            goal_plus_id,
            run_id=run_id,
            selected_candidate_id=selected_candidate_id,
            report_path=report_path,
            promotion_artifact_path=promotion_artifact_path,
            summary=summary,
        ).model_dump(mode="json")

    def goal_plus_set_status(
        self,
        goal_plus_id: str,
        status: str,
        reason: str | None = None,
        evidence: list[dict[str, Any]] | None = None,
        next_action: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parsed_next_action = (
            GoalPlusNextAction.model_validate(next_action)
            if next_action is not None
            else None
        )
        return self.runtime.set_status(
            goal_plus_id,
            status=status,  # type: ignore[arg-type]
            reason=reason,
            evidence=evidence,
            next_action=parsed_next_action,
        ).model_dump(mode="json")

    def goal_plus_gate(
        self,
        goal_plus_id: str,
        event: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return self.runtime.gate(
            goal_plus_id,
            event=event,  # type: ignore[arg-type]
            context=context,
        ).model_dump(mode="json")
