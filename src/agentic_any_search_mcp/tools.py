from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_any_search_mcp.models import CandidateProposal, SearchSpec
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
