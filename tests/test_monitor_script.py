from __future__ import annotations

import json
from pathlib import Path
import subprocess

from goal_plus.runtime import FileSearchRuntime
from tests._runtime_helpers import make_project, spec_for


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "monitor_goal_plus.sh"


def _search_fixture(tmp_path: Path) -> tuple[Path, str, str]:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(project / ".gp")
    frozen = runtime.freeze_spec(
        spec_for(project, max_candidates=1),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)
    (task.workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        task.candidate_id,
        agent_session_id=session.agent_session_id,
        hypothesis="monitor script verifier evidence",
    )
    return project, run_id, task.candidate_id


def test_monitor_script_discovers_project_runtime_and_renders_detail(
    tmp_path: Path,
) -> None:
    project, run_id, candidate_id = _search_fixture(tmp_path)

    result = subprocess.run(
        [str(SCRIPT), "--once", "--no-clear", str(project)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"runtime:   {project / '.gp'}" in result.stdout
    assert f"run {run_id}:" in result.stdout
    assert f"{candidate_id}: worker=" in result.stdout
    assert "candidate=evaluated" in result.stdout
    assert "decision=" in result.stdout
    assert "latest i1" in result.stdout
    assert "monitor script verifier evidence" in result.stdout
    assert "agent=agent_" in result.stdout

    verbose = subprocess.run(
        [str(SCRIPT), "--once", "--no-clear", "--verbose", str(project)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert verbose.returncode == 0, verbose.stderr
    assert "latest iteration: i1" in verbose.stdout
    assert "usage: active=" in verbose.stdout


def test_monitor_script_json_mode_returns_assembled_snapshot(tmp_path: Path) -> None:
    project, run_id, candidate_id = _search_fixture(tmp_path)

    result = subprocess.run(
        [str(SCRIPT), "--json", str(project / ".gp")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["runtime_root"] == str(project / ".gp")
    [run] = payload["runs"]
    assert run["snapshot"]["run"]["run_id"] == run_id
    assert candidate_id in run["candidate_records"]
    assert run["candidate_records"][candidate_id]["iterations"][0]["hypothesis"] == (
        "monitor script verifier evidence"
    )


def test_monitor_script_reports_missing_runtime_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(SCRIPT), "--once", str(tmp_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "no Goal Plus runtime root found" in result.stderr
