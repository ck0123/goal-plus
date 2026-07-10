from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = ROOT / "examples" / "workspace-backends"
sys.path.insert(0, str(ROOT / "src"))

from agentic_any_search_mcp.runtime import FileSearchRuntime  # noqa: E402
from agentic_any_search_mcp.tools import SearchTools  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the host-free Git worktree workspace E2E demo."
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        required=True,
        help="Empty or new path for file-backed Search runtime state.",
    )
    return parser.parse_args()


def _write_value(workspace: Path, value: int) -> None:
    (workspace / "initial_program.py").write_text(
        f"VALUE = {value}\n", encoding="utf-8"
    )


def _git_common_dir(workspace: Path) -> Path:
    raw = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=workspace,
            text=True,
        ).strip()
    )
    return raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()


def run_demo(runtime_root: Path) -> dict[str, object]:
    spec = json.loads(
        (EXAMPLE_DIR / "git_worktree_search_spec.json").read_text(encoding="utf-8")
    )
    spec["source_path"] = str(EXAMPLE_DIR / "source")
    tools = SearchTools(FileSearchRuntime(runtime_root))
    frozen = tools.search_freeze_spec(
        spec,
        [str(EXAMPLE_DIR / "source" / "evaluator.py")],
    )
    run_id = tools.search_create(frozen["frozen_spec_id"])["run_id"]

    first_plan = tools.search_plan_next(run_id, 2)
    first = tools.search_start_batch(run_id, first_plan["plan_id"])
    scores: dict[str, float] = {}
    branches: dict[str, str] = {}
    workspaces: dict[str, Path] = {}
    for task, value in zip(first, (1, 2), strict=True):
        candidate_id = task["candidate_id"]
        workspace = Path(task["workspace"])
        workspaces[candidate_id] = workspace
        branches[candidate_id] = task["workspace_branch"]
        _write_value(workspace, value)
        report = tools.search_run_verifier(run_id, candidate_id)
        scores[candidate_id] = report["aggregate_score"]

    parent_iterations = tools.search_list_iterations(run_id, "c002")
    parent_best_git_head = parent_iterations[0]["git_head"]
    second_plan = tools.search_plan_next(run_id, 1)
    child = tools.search_start_batch(run_id, second_plan["plan_id"])[0]
    child_workspace = Path(child["workspace"])
    workspaces["c003"] = child_workspace
    branches["c003"] = child["workspace_branch"]
    _write_value(child_workspace, 3)
    child_report = tools.search_run_verifier(run_id, "c003")
    scores["c003"] = child_report["aggregate_score"]

    selection = tools.search_select(run_id)
    report = tools.search_report(run_id)
    common_dirs = {_git_common_dir(path) for path in workspaces.values()}
    return {
        "run_id": run_id,
        "workspace_backend": "git_worktree",
        "candidate_ids": list(workspaces),
        "scores": scores,
        "branches": branches,
        "shared_git_common_dir": len(common_dirs) == 1,
        "parent_candidate_id": second_plan["strategy_trace"][
            "parent_candidate_id"
        ],
        "parent_best_git_head": parent_best_git_head,
        "child_base_revision": child["workspace_base_revision"],
        "selected_candidate_id": selection["selected_candidate_id"],
        "report_path": report["report_path"],
    }


def main() -> int:
    args = parse_args()
    print(json.dumps(run_demo(args.runtime_root.resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
