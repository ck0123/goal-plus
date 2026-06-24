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
    def search_next_batch(run_id: str, k: int = 4) -> list[dict[str, Any]]:
        return tools.search_next_batch(run_id, k)

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
