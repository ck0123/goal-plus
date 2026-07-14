from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.st.helpers.codex_runner import CodexRunner


pytestmark = [pytest.mark.st, pytest.mark.st_codex]


def _write_spec_revision_workspace(workspace: Path) -> None:
    verifier_dir = workspace / ".goal-plus-verifiers"
    verifier_dir.mkdir(parents=True)
    workspace.joinpath("candidate.txt").write_text("0\n", encoding="utf-8")
    for name, cap in (("simple_score.py", 10), ("strict_score.py", 100)):
        verifier_dir.joinpath(name).write_text(
            "from __future__ import annotations\n"
            "import json\n"
            "from pathlib import Path\n"
            f"CAP = {cap}\n"
            "value = int(Path('candidate.txt').read_text(encoding='utf-8').strip())\n"
            "print(json.dumps({'score': min(value, CAP)}))\n",
            encoding="utf-8",
        )


def test_codex_goal_plus_revises_goal_and_frozen_spec_after_first_result(
    st_project_root: Path,
    st_log_dir: Path,
) -> None:
    search_root = st_project_root / ".gp"
    workspace = st_project_root / "spec-revision-workspace"
    shutil.rmtree(search_root, ignore_errors=True)
    shutil.rmtree(workspace, ignore_errors=True)
    _write_spec_revision_workspace(workspace)

    runner = CodexRunner(
        project_root=st_project_root,
        log_dir=st_log_dir,
        default_timeout=1200,
    )
    initial_goal = (
        "Use a verifier-guided Search run to maximize score in candidate.txt with "
        ".goal-plus-verifiers/simple_score.py. Only candidate.txt may be edited. "
        "After the first complete result, reassess whether that frozen spec proves "
        "the deeper objective before completing the Goal Plus record."
    )
    revised_goal = (
        "The first result demonstrated relative improvement under the simple capped "
        "proxy but did not prove the deeper objective. Run a second verifier-guided "
        "Search using .goal-plus-verifiers/strict_score.py to maximize score in the "
        "same candidate.txt. Only candidate.txt may be edited. Complete only after "
        "the second result is selected, reported, promoted, and recorded."
    )
    result = runner.run_streaming(
        (
            "Create exactly one Goal Plus record with goal_plus_create using this initial "
            f"raw goal and source_path={str(workspace)!r}:\n{initial_goal}\n\n"
            "For the first SearchSpec, use metric_name=score, direction=maximize, "
            "workspace.backend=copy, process verifier "
            ".goal-plus-verifiers/simple_score.py, no promotion verifiers, and allow only "
            "candidate.txt. Use strategy.name=random, driver=builtin, worker_host=codex, "
            "worker_mode=agent-session-pool, worker_agent_type=search_candidate_agent, "
            "max_candidates=1, max_parallel=1, and worker_budget with "
            "max_runtime_seconds=90, max_turns=4, on_exceed=interrupt. Freeze the exact "
            "verifier artifact and complete the full Search flow through selection, report, "
            "promotion, and goal_plus_record_search_result. Direct the candidate worker to "
            "set candidate.txt to an integer that reaches the simple verifier cap.\n\n"
            "After the first meaningful result, use the existing raw-goal audit and choose "
            "revise_goal because a large relative improvement under the simple capped proxy "
            "does not prove the deeper objective. Call goal_plus_update_goal with "
            "expected_revision=1 and this complete revised raw goal:\n"
            f"{revised_goal}\n\n"
            "Re-triage revision 2, save a new high-confidence draft, and freeze a different "
            "SearchSpec that is identical except that it uses and freezes "
            ".goal-plus-verifiers/strict_score.py. Create and link a different run_id, then "
            "complete the full second Search flow. Direct the second candidate worker to set "
            "candidate.txt to an integer that reaches the strict verifier cap. Keep the first "
            "Search task only as historical evidence. Mark Goal Plus complete only after both "
            "runs are promoted and both results are recorded."
        ),
        scenario="codex_goal_plus_spec_revision",
        timeout=1200,
    )

    print(f"\n[codex_goal_plus_spec_revision] log: {result.log_path}")
    assert not result.timed_out, result.log_path
    assert result.returncode == 0, result.log_path

    goal_files = sorted(search_root.glob("goal-plus/gp_*/goal.json"))
    assert len(goal_files) == 1, result.log_path
    record = json.loads(goal_files[0].read_text(encoding="utf-8"))
    assert record["status"] == "complete", result.log_path
    assert record["goal_revision"] == 2
    assert len(record["goal_revisions"]) == 2

    search_tasks = record.get("search_tasks") or []
    assert len(search_tasks) == 2, record
    assert [task["goal_revision"] for task in search_tasks] == [1, 2]
    frozen_spec_ids = [task["frozen_spec_id"] for task in search_tasks]
    run_ids = [task["run_id"] for task in search_tasks]
    assert len(set(frozen_spec_ids)) == 2
    assert len(set(run_ids)) == 2

    verifier_names = []
    selected_scores = []
    for task in search_tasks:
        run_record = json.loads(
            (search_root / "runs" / task["run_id"] / "run.json").read_text(
                encoding="utf-8"
            )
        )
        assert run_record["state"] == "promoted"
        assert task.get("result_recorded_at")
        assert Path(task["report_path"]).exists()
        assert Path(task["promotion_artifact_path"]).exists()
        selected_scores.append(run_record["selected_score"])
        frozen_spec = json.loads(
            (
                search_root
                / "specs"
                / task["frozen_spec_id"]
                / "frozen_spec.json"
            ).read_text(encoding="utf-8")
        )
        verifier_names.append(
            Path(frozen_spec["spec"]["process_verifiers"][0]["command"][-1]).name
        )

    assert verifier_names == ["simple_score.py", "strict_score.py"]
    assert selected_scores[0] == 10.0
    assert selected_scores[1] == 100.0
    events = [
        json.loads(line)
        for line in goal_files[0].with_name("events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    event_types = [event["event_type"] for event in events]
    assert event_types.count("goal_updated") == 1
    assert event_types.count("triage_recorded") == 2
