from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_any_search_mcp.models import ArtifactBundle, CandidateProposal, SearchSpec
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
        candidate_id: str | None = None,
        directive: dict[str, Any] | str | None = None,
        budget: dict[str, Any] | None = None,
        visibility_mode: str = "observations",
    ) -> dict[str, Any]:
        return self.runtime.start_agent_session(
            run_id=run_id,
            candidate_id=candidate_id,
            directive=directive,
            budget=budget,
            visibility_mode=visibility_mode,
        ).model_dump(mode="json")

    def search_get_agent_context(self, agent_session_id: str) -> dict[str, Any]:
        return self.runtime.get_agent_context(agent_session_id)

    def search_update_agent_status(
        self,
        agent_session_id: str,
        phase: str,
        current_goal: str = "",
        last_action: str = "",
        next_step: str = "",
        blockers: list[str] | None = None,
        status: str | None = None,
        heartbeat: bool = True,
    ) -> dict[str, Any]:
        session = self.runtime.update_agent_status(
            agent_session_id=agent_session_id,
            phase=phase,
            current_goal=current_goal,
            last_action=last_action,
            next_step=next_step,
            blockers=blockers,
            status=status,
            heartbeat=heartbeat,
        )
        return self._agent_session_ack(session)

    def search_list_agent_status(
        self,
        run_id: str,
        include_stale: bool = True,
    ) -> list[dict[str, Any]]:
        return [
            session.model_dump(mode="json")
            for session in self.runtime.list_agent_status(run_id, include_stale=include_stale)
        ]

    @staticmethod
    def _agent_session_ack(session: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "agent_session_id": session.agent_session_id,
            "run_id": session.run_id,
            "candidate_id": session.candidate_id,
            "status": session.status,
            "phase": session.phase,
            "updated_at": session.updated_at,
        }

    def search_finish_agent_session(
        self,
        agent_session_id: str,
        status: str = "completed",
        summary: str = "",
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.runtime.finish_agent_session(
            agent_session_id=agent_session_id,
            status=status,
            summary=summary,
            result=result,
        ).model_dump(mode="json")

    def search_abort_agent_session(
        self,
        agent_session_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        return self.runtime.abort_agent_session(agent_session_id, reason).model_dump(mode="json")

    def search_abort_all_agent_sessions(
        self,
        run_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        sessions = self.runtime.abort_all_agent_sessions(run_id, reason)
        return {
            "aborted": len(sessions),
            "sessions": [session.model_dump(mode="json") for session in sessions],
        }

    def search_publish_observation(
        self,
        agent_session_id: str,
        summary: str,
        evidence: str = "",
        next_ideas: list[str] | None = None,
        tags: list[str] | None = None,
        visibility: str = "observations",
    ) -> dict[str, Any]:
        return self.runtime.publish_observation(
            agent_session_id=agent_session_id,
            summary=summary,
            evidence=evidence,
            next_ideas=next_ideas,
            tags=tags,
            visibility=visibility,
        ).model_dump(mode="json")

    def search_list_observations(
        self,
        run_id: str,
        visibility: str | None = None,
        tags: list[str] | None = None,
        top_n: int = 20,
    ) -> list[dict[str, Any]]:
        return self.runtime.list_observations(
            run_id=run_id,
            visibility=visibility,
            tags=tags,
            top_n=top_n,
        )

    def search_wait_agent_events(
        self,
        run_id: str,
        timeout_seconds: int = 300,
        wake_on: list[str] | None = None,
        since_event_id: str | None = None,
        return_when_all_idle: bool = True,
    ) -> dict[str, Any]:
        return self.runtime.wait_agent_events(
            run_id=run_id,
            timeout_seconds=timeout_seconds,
            wake_on=wake_on,
            since_event_id=since_event_id,
            return_when_all_idle=return_when_all_idle,
        ).model_dump(mode="json")

    def search_submit_candidate(
        self,
        run_id: str,
        candidate_id: str,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        self.runtime.submit_candidate(
            run_id=run_id,
            candidate_id=candidate_id,
            artifact=ArtifactBundle.model_validate(artifact),
        )
        return {"accepted": True}

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
