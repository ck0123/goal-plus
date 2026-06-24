from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_any_search_mcp.models import ArtifactBundle, SearchSpec
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

    def search_next_batch(self, run_id: str, k: int = 4) -> list[dict[str, Any]]:
        return [task.model_dump(mode="json") for task in self.runtime.next_batch(run_id, k)]

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
    ) -> dict[str, Any]:
        report = self.runtime.run_verifier(run_id, candidate_id, scope=scope)  # type: ignore[arg-type]
        return report.model_dump(mode="json")

    def search_select(self, run_id: str, strategy: str = "independent_branches") -> dict[str, Any]:
        return self.runtime.select(run_id, strategy=strategy)

    def search_report(self, run_id: str) -> dict[str, str]:
        return {"report_path": str(self.runtime.report(run_id))}

    def search_promote(self, run_id: str, candidate_id: str) -> dict[str, str]:
        return {"artifact_path": str(self.runtime.promote(run_id, candidate_id))}

    def search_abort(self, run_id: str, reason: str = "") -> dict[str, bool]:
        self.runtime.abort(run_id, reason)
        return {"aborted": True}

