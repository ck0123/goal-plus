from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from goal_plus.models import (
    CandidateProposal,
    CandidateRecord,
    RunState,
    ScoreReport,
    SearchSpec,
)
from goal_plus.runtime import (
    FileSearchRuntime,
    canonical_json,
    copy_source_tree,
    list_files,
    load_json,
    path_matches,
    sha256_file,
    write_json,
)


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "initial_program.py").write_text("VALUE = 0\n", encoding="utf-8")
    (project / "evaluator.py").write_text(
        "import json\n"
        "def evaluate(_path):\n"
        "    return {'combined_score': 0.0}\n"
        "if __name__ == '__main__':\n"
        "    print(json.dumps(evaluate('initial_program.py')))\n",
        encoding="utf-8",
    )
    (project / "config.yaml").write_text("name: toy\n", encoding="utf-8")
    return project


def spec_for(project: Path, *, max_candidates: int = 4, direction: str = "maximize") -> SearchSpec:
    return SearchSpec.model_validate(
        {
            "objective": "test runtime",
            "metric_name": "combined_score",
            "metric_direction": direction,
            "source_path": str(project),
            "edit_surface": {
                "allow": ["initial_program.py"],
                "deny": ["evaluator.py", "config.yaml"],
            },
            "budget": {
                "max_candidates": max_candidates,
                "max_parallel": max_candidates,
            },
            "process_verifiers": [
                {
                    "name": "score",
                    "role": "ranking_signal",
                    "command": ["python", "evaluator.py"],
                    "timeout_seconds": 30,
                }
            ],
            "strategy": {"name": "independent_branches"},
            "workspace": {"backend": "copy"},
        }
    )


def spec_with_strategy(
    project: Path,
    strategy: dict,
    *,
    max_candidates: int = 4,
) -> SearchSpec:
    data = spec_for(project, max_candidates=max_candidates).model_dump(mode="json")
    data["strategy"] = strategy
    return SearchSpec.model_validate(data)


def spec_with_host(
    project: Path,
    worker_host: str,
    *,
    strategy_name: str = "agent_guided",
    max_candidates: int = 4,
) -> SearchSpec:
    return spec_with_strategy(
        project,
        {
            "name": strategy_name,
            "worker_mode": "agent-session-pool",
            "worker_host": worker_host,
        },
        max_candidates=max_candidates,
    )


def create_candidate(
    runtime: FileSearchRuntime,
    project: Path,
    *,
    direction: str = "maximize",
) -> tuple[str, str, Path]:
    frozen = runtime.freeze_spec(spec_for(project, direction=direction), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    return run_id, task.candidate_id, task.workspace


def git_commit_all(workspace: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-q",
            "--no-verify",
            "-m",
            message,
        ],
        cwd=workspace,
        check=True,
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True
    ).strip()


def test_hash_json_and_path_helpers(tmp_path: Path) -> None:
    file_path = tmp_path / "a.txt"
    file_path.write_text("hello\n", encoding="utf-8")

    assert sha256_file(file_path) == sha256_file(file_path)
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
    assert path_matches("src/app.py", ["src/"])
    assert path_matches("initial_program.py", ["*.py"])
    assert not path_matches("evaluator.py", ["initial_program.py"])


def test_copy_source_tree_and_list_files_ignore_runtime_noise(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (source / ".gp").mkdir()
    (source / ".gp" / "run.json").write_text("{}", encoding="utf-8")
    (source / ".search").mkdir()
    (source / ".search" / "run.json").write_text("{}", encoding="utf-8")
    (source / ".tmp").mkdir()
    (source / ".tmp" / "scratch.py").write_text("print('scratch')\n", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "keep.pyc").write_text("compiled", encoding="utf-8")

    destination = tmp_path / "dest"
    copy_source_tree(source, destination)

    listed = [path.relative_to(destination).as_posix() for path in list_files(destination)]
    assert listed == ["keep.py"]


def test_file_search_runtime_defaults_to_gp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = FileSearchRuntime()

    assert runtime.root_dir == tmp_path / ".gp"
    assert runtime.specs_dir == tmp_path / ".gp" / "specs"
    assert runtime.runs_dir == tmp_path / ".gp" / "runs"


def test_write_json_is_readable(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "data.json"
    write_json(path, {"ok": True})
    assert path.read_text(encoding="utf-8").strip().startswith("{")


def test_freeze_spec_is_stable_and_copies_verifier(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_for(project)

    first = runtime.freeze_spec(spec, [project / "evaluator.py"])
    second = runtime.freeze_spec(spec, [project / "evaluator.py"])

    assert first.frozen_spec_id == second.frozen_spec_id
    assert first.verifier_hashes["evaluator.py"] == sha256_file(project / "evaluator.py")
    assert Path(first.frozen_verifier_paths["evaluator.py"]).exists()


def test_freeze_spec_rejects_verifier_workspace_side_effect_without_mutating_source(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "output = Path('.goal-plus-verifiers/generated.bin')\n"
        "output.parent.mkdir(parents=True, exist_ok=True)\n"
        "output.write_text('compiled', encoding='utf-8')\n"
        "print(json.dumps({'combined_score': 1.0}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")

    with pytest.raises(ValueError, match="VerifierWorkspaceSideEffect") as exc_info:
        runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])

    assert ".goal-plus-verifiers/generated.bin" in str(exc_info.value)
    assert "GOAL_PLUS_VERIFIER_TMPDIR" in str(exc_info.value)
    assert "concurrently" in str(exc_info.value)
    assert not (project / ".goal-plus-verifiers").exists()
    assert list(runtime.specs_dir.iterdir()) == []


def test_freeze_spec_provides_per_invocation_verifier_temp_directory(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "names = ('TMPDIR', 'TMP', 'TEMP', 'GOAL_PLUS_VERIFIER_TMPDIR')\n"
        "paths = {os.environ[name] for name in names}\n"
        "assert len(paths) == 1\n"
        "tmp = Path(paths.pop())\n"
        "assert tmp.is_dir()\n"
        "assert os.environ['GOAL_PLUS_VERIFIER_PHASE'] == 'freeze_preflight'\n"
        "tmp.joinpath('compiled.bin').write_text('ok', encoding='utf-8')\n"
        "print(json.dumps({'combined_score': 1.0}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")

    frozen = runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])

    assert frozen.frozen_spec_id.startswith("spec_")
    assert not (project / "compiled.bin").exists()


def test_freeze_spec_rejects_verifier_outputs_in_workspace_tmp(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "output = Path('.tmp/verifier-output.txt')\n"
        "output.parent.mkdir(parents=True, exist_ok=True)\n"
        "output.write_text('not worker scratch', encoding='utf-8')\n"
        "print(json.dumps({'combined_score': 1.0}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")

    with pytest.raises(ValueError, match="VerifierWorkspaceSideEffect") as exc_info:
        runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])

    assert ".tmp/verifier-output.txt" in str(exc_info.value)
    assert not (project / ".tmp").exists()


def test_freeze_spec_rejects_plain_text_ranking_score(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "print('Score = 18941966307')\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")

    with pytest.raises(ValueError, match="emitted no finite numeric metric") as exc_info:
        runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])

    assert '{"combined_score":123.0}' in str(exc_info.value)
    assert "expected_outputs lists artifact paths only" in str(exc_info.value)
    assert list(runtime.specs_dir.iterdir()) == []


@pytest.mark.parametrize(
    "metric_value",
    ["'18941966307'", "True", "float('nan')", "float('inf')"],
)
def test_freeze_spec_rejects_non_numeric_or_non_finite_ranking_score(
    tmp_path: Path,
    metric_value: str,
) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        f"print(json.dumps({{'combined_score': {metric_value}}}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")

    with pytest.raises(ValueError, match="emitted no finite numeric metric"):
        runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])


def test_null_verifier_error_is_accepted_by_preflight_and_runtime(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "print(json.dumps({'combined_score': 7.0, 'error': None}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")

    frozen = runtime.freeze_spec(
        spec_for(project, max_candidates=1),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    report = runtime.run_verifier(run_id, task.candidate_id)

    assert report.process_passed is True
    assert report.aggregate_score == 7.0
    assert report.verifier_results[0].metrics["error"] is None


def test_freeze_spec_rejects_non_null_verifier_error(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "print(json.dumps({'combined_score': 7.0, 'error': 'broken evaluator'}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")

    with pytest.raises(ValueError, match="reported an error.*broken evaluator"):
        runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])

    assert list(runtime.specs_dir.iterdir()) == []


def test_runtime_rejects_non_null_verifier_error_after_clean_preflight(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "from initial_program import VALUE\n"
        "error = None if VALUE == 0 else 'candidate evaluation failed'\n"
        "print(json.dumps({'combined_score': 7.0, 'error': error}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        spec_for(project, max_candidates=1),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    task.workspace.joinpath("initial_program.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    report = runtime.run_verifier(run_id, task.candidate_id)

    assert report.process_passed is False
    assert report.aggregate_score == 0.0
    assert report.verifier_results[0].failure_class == "VerifierCommandFailed"
    assert report.verifier_results[0].metrics["error"] == "candidate evaluation failed"


@pytest.mark.parametrize("runtime_dir", [".gp", ".search"])
def test_freeze_spec_rejects_verifier_artifact_under_runtime_root(
    tmp_path: Path,
    runtime_dir: str,
) -> None:
    project = make_project(tmp_path)
    verifier = project / runtime_dir / "verifiers" / "score.sh"
    verifier.parent.mkdir(parents=True)
    verifier.write_text(
        "#!/usr/bin/env bash\nprintf '{\"combined_score\": 1}\\n'\n",
        encoding="utf-8",
    )
    spec_data = spec_for(project).model_dump(mode="json")
    spec_data["process_verifiers"][0]["command"] = [
        "bash",
        f"{runtime_dir}/verifiers/score.sh",
    ]
    runtime = FileSearchRuntime(tmp_path / ".search")

    with pytest.raises(
        ValueError,
        match=f"ignored Goal Plus runtime directory '{runtime_dir}'",
    ):
        runtime.freeze_spec(SearchSpec.model_validate(spec_data), [verifier])


def test_freeze_spec_rejects_verifier_artifact_outside_source_path(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    verifier = tmp_path / "external-verifier.py"
    verifier.write_text(
        "import json\nprint(json.dumps({'combined_score': 1}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")

    with pytest.raises(ValueError, match="outside source_path"):
        runtime.freeze_spec(spec_for(project), [verifier])


def test_source_owned_verifier_is_present_in_git_worktree_candidate(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    verifier = project / ".goal-plus-verifiers" / "score.sh"
    verifier.parent.mkdir()
    verifier.write_text(
        "#!/usr/bin/env bash\nprintf '{\"combined_score\": 1}\\n'\n",
        encoding="utf-8",
    )
    spec_data = spec_for(project, max_candidates=1).model_dump(mode="json")
    spec_data["workspace"] = {"backend": "git_worktree"}
    spec_data["process_verifiers"][0]["command"] = [
        "bash",
        ".goal-plus-verifiers/score.sh",
    ]
    spec_data["edit_surface"]["deny"].append(".goal-plus-verifiers/")
    runtime = FileSearchRuntime(tmp_path / ".search")

    frozen = runtime.freeze_spec(SearchSpec.model_validate(spec_data), [verifier])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    candidate_verifier = task.workspace / ".goal-plus-verifiers" / "score.sh"

    assert candidate_verifier.exists()
    assert sha256_file(candidate_verifier) == frozen.verifier_hashes[
        ".goal-plus-verifiers/score.sh"
    ]


def test_create_run_reuses_frozen_verifier_with_current_source_baseline(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        spec_for(project, max_candidates=1),
        [project / "evaluator.py"],
    )
    first_run_id = runtime.create_run(frozen.frozen_spec_id)
    project.joinpath("initial_program.py").write_text(
        "VALUE = 7\n",
        encoding="utf-8",
    )

    second_run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(second_run_id, requested_k=1)
    task = runtime.start_batch(second_run_id, plan.plan_id)[0]

    assert second_run_id != first_run_id
    assert task.workspace.joinpath("initial_program.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 7\n"
    assert sha256_file(task.workspace / "evaluator.py") == frozen.verifier_hashes[
        "evaluator.py"
    ]


def test_runtime_marks_missing_ranking_metric_as_failure(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "if 'VALUE = 0' in Path('initial_program.py').read_text():\n"
        "    print(json.dumps({'combined_score': 0}))\n"
        "else:\n"
        "    print('Score = 42')\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    task.workspace.joinpath("initial_program.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    report = runtime.run_verifier(run_id, task.candidate_id)

    result = report.verifier_results[0]
    assert report.process_passed is False
    assert report.aggregate_score == 0.0
    assert result.failure_class == "MissingNumericMetric"
    assert result.metrics["expected_metric_name"] == "combined_score"
    assert result.metrics["stdout_tail"] == "Score = 42"


def test_select_chooses_highest_json_ranking_metric(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "from initial_program import VALUE\n"
        "print(json.dumps({'combined_score': VALUE}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=3), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=3)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    for task, score in zip(tasks, [10, 20, 30], strict=True):
        task.workspace.joinpath("initial_program.py").write_text(
            f"VALUE = {score}\n",
            encoding="utf-8",
        )
        report = runtime.run_verifier(run_id, task.candidate_id)
        assert report.aggregate_score == score

    selection = runtime.select(run_id)

    assert selection["selected_candidate_id"] == "c003"
    assert selection["selected_score"] == 30.0


def test_load_legacy_frozen_spec_without_workspace_uses_copy_backend(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])
    frozen_path = runtime._spec_dir(frozen.frozen_spec_id) / "frozen_spec.json"
    frozen_data = load_json(frozen_path)
    frozen_data["spec"].pop("workspace")
    write_json(frozen_path, frozen_data)

    loaded = runtime._load_frozen_spec(frozen.frozen_spec_id)

    assert loaded.spec.workspace.backend == "copy"


def test_runtime_defaults_to_git_worktree_workspace(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec_data = spec_for(project, max_candidates=1).model_dump(mode="json")
    spec_data.pop("workspace")
    spec = SearchSpec.model_validate(spec_data)

    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    assert task.workspace_backend == "git_worktree"
    assert task.workspace_branch == f"gp/{run_id}/c001"
    common_dir = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=task.workspace,
        text=True,
    ).strip()
    assert (task.workspace / common_dir).resolve() == (
        runtime._run_dir(run_id) / "workspace-repository" / ".git"
    ).resolve()


def test_freeze_spec_normalizes_verifier_cwd_equal_to_source_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    project = repo / "examples" / "model-optimize" / "torch-cpu-target"
    project.mkdir(parents=True)
    (project / "initial_program.py").write_text("VALUE = 0\n", encoding="utf-8")
    (project / "evaluator.py").write_text(
        "import json\nprint(json.dumps({'combined_score': 1.0}))\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    spec_data = spec_for(project, max_candidates=1).model_dump(mode="json")
    spec_data["source_path"] = "examples/model-optimize/torch-cpu-target"
    spec_data["process_verifiers"][0]["cwd"] = "examples/model-optimize/torch-cpu-target"
    spec_data["promotion_verifiers"] = [
        {
            "name": "promotion",
            "role": "promotion_gate",
            "command": ["python", "evaluator.py"],
            "cwd": "examples/model-optimize/torch-cpu-target",
        }
    ]
    runtime = FileSearchRuntime(tmp_path / ".search")

    frozen = runtime.freeze_spec(
        SearchSpec.model_validate(spec_data),
        [project / "evaluator.py"],
    )

    assert frozen.spec.process_verifiers[0].cwd == "."
    assert frozen.spec.promotion_verifiers[0].cwd == "."


def test_plan_next_and_start_batch_record_plan_metadata(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=2), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    assert plan.strategy.name == "independent_branches"
    assert plan.worker_policy["mode"] == "agent-session-pool"
    assert plan.worker_policy["requires_agent_session"] is True
    assert plan.planned_k == 2
    assert [task.candidate_id for task in tasks] == ["c001", "c002"]
    assert tasks[0].plan_id == "plan_001"
    assert tasks[0].proposal.intent == "Independent candidate c001"  # type: ignore[union-attr]
    assert (tasks[0].workspace / ".tmp").is_dir()
    assert any(".tmp" in instruction for instruction in tasks[0].instructions)

    saved_plan = runtime._load_plan(run_id, "plan_001")
    assert saved_plan.status == "started"
    assert saved_plan.started_candidate_ids == ["c001", "c002"]


def test_candidate_workspace_has_isolated_git_baseline_under_ignored_parent(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=parent, check=True)
    (parent / ".gitignore").write_text(".tmp/\n", encoding="utf-8")

    project = make_project(parent)
    runtime = FileSearchRuntime(parent / ".tmp" / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=task.workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert Path(root) == task.workspace

    (task.workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")

    status = subprocess.run(
        ["git", "status", "--short", "initial_program.py"],
        cwd=task.workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "diff", "--", "initial_program.py"],
        cwd=task.workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert status.startswith("M ")
    assert "VALUE = 1" in diff
    assert runtime._detect_changed_files(project, task.workspace) == ["initial_program.py"]


def test_worker_policy_documents_agent_session_pool(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "SearchCandidateAgent",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    assert plan.worker_policy["mode"] == "agent-session-pool"
    assert plan.worker_policy["subagent_type"] == "SearchCandidateAgent"
    assert plan.worker_policy["requires_agent_session"] is True
    assert tasks[0].strategy_metadata["worker_mode"] == "agent-session-pool"
    assert any(
        "Pass context.agent_session_id to search_run_verifier" in instruction
        for instruction in tasks[0].instructions
    )
    assert any(
        "search_run_verifier" in instruction for instruction in tasks[0].instructions
    )
    assert any(
        "git repository has already been initialized" in instruction
        for instruction in tasks[0].instructions
    )
    assert any(
        "iteration log" in instruction for instruction in tasks[0].instructions
    )
    combined_instructions = "\n".join(tasks[0].instructions)
    assert "Complete and verify a candidate early" in combined_instructions
    assert "leave enough time to return a concise summary" in combined_instructions
    assert "When steps run out the host will ask you" not in combined_instructions


def test_promote_requires_search_runtime_selection(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    run_id, candidate_id, workspace = create_candidate(runtime, project)
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    report = runtime.run_verifier(run_id, candidate_id)
    assert report.process_passed is True

    with pytest.raises(RuntimeError, match="search_select"):
        runtime.promote(run_id, candidate_id)

    selected = runtime.select(run_id)
    assert selected["selected_candidate_id"] == candidate_id
    assert runtime.promote(run_id, candidate_id).exists()


@pytest.mark.codex
def test_worker_policy_includes_host_capabilities_for_codex(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, "codex", strategy_name="random", max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan = runtime.plan_next(run_id, requested_k=1)

    assert plan.worker_policy["host"] == "codex"
    assert plan.worker_policy["supports_same_worker_continue"] is True
    assert plan.worker_policy["uses_background_workers"] is False
    assert plan.worker_policy["pool"]["launch_mode"] == "async"
    assert plan.worker_policy["pool"]["wait_mode"] == "wait_any"
    assert plan.worker_policy["pool"]["continuation_mode"] == "same_worker"
    assert plan.worker_policy["pool"]["wait_tool"] == "wait_agent"
    assert plan.worker_policy["pool"]["continue_tool"] == "followup_task"


@pytest.mark.pi
def test_worker_policy_uses_pi_rpc_state_redispatch(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {
                "max_runtime_seconds": 600,
                "max_turns": 8,
                "on_exceed": "interrupt",
            },
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan = runtime.plan_next(run_id, requested_k=1)

    assert plan.worker_policy["host"] == "pi-rpc"
    assert plan.worker_policy["supports_same_worker_continue"] is False
    assert plan.worker_policy["continuation"] == "state_redispatch"
    assert plan.worker_policy["uses_background_workers"] is False
    assert plan.worker_policy["pool"]["launch_mode"] == "async"
    assert plan.worker_policy["pool"]["wait_mode"] == "wait_any"
    assert plan.worker_policy["pool"]["continuation_mode"] == "state_redispatch"
    assert plan.worker_policy["pool"]["recovery_mode"] == "supervisor_persisted"
    assert plan.worker_policy["pool"]["wait_tool"] == "pi_search_pool_wait_any"


def test_start_agent_session_creates_context_handle_and_launch_payload(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "SearchCandidateAgent",
        },
        max_candidates=1,
    )
    spec_data = spec.model_dump(mode="json")
    spec_data["budget"]["max_parallel"] = 1
    frozen = runtime.freeze_spec(SearchSpec.model_validate(spec_data), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    session = runtime.start_agent_session(
        run_id, tasks[0].candidate_id, {"goal": "try one concrete variant"},
    )
    assert session.candidate_id == tasks[0].candidate_id
    assert session.workspace == tasks[0].workspace
    assert session.agent_session_id.startswith("agent_")
    assert session.launch["subagent_type"] == "SearchCandidateAgent"
    assert session.host == "opencode"
    assert session.host_handle.host == "opencode"
    assert tasks[0].candidate_id in session.launch["description"]
    assert session.agent_session_id in session.launch["prompt"]
    assert tasks[0].candidate_id in session.launch["prompt"]
    assert "required" not in session.launch


def test_redispatch_candidate_creates_new_session_with_tier_override(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "SearchCandidateAgentFlash",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    first = runtime.start_agent_session(run_id, task.candidate_id, {"goal": "try flash"})

    redispatched = runtime.redispatch_candidate(
        run_id,
        task.candidate_id,
        {"goal": "resume with more steps"},
        worker_agent_type="SearchCandidateAgentDeep",
    )

    assert redispatched.agent_session_id != first.agent_session_id
    assert redispatched.candidate_id == first.candidate_id
    assert redispatched.workspace == first.workspace
    assert redispatched.launch["subagent_type"] == "SearchCandidateAgentDeep"
    assert redispatched.agent_session_id in redispatched.launch["prompt"]
    assert "state_level_resume" in redispatched.launch["prompt"]

    refreshed_candidate = runtime._load_candidate_record(run_id, task.candidate_id)
    worker_policy = refreshed_candidate.task.strategy_metadata["worker_policy"]
    assert worker_policy["worker_agent_type"] == "SearchCandidateAgentFlash"


def test_redispatch_context_includes_previous_progress_handoff(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec_data = spec_with_host(
        project, "pi-rpc", strategy_name="random", max_candidates=1
    ).model_dump(mode="json")
    spec_data["strategy"]["worker_budget"] = {
        "max_runtime_seconds": 60,
        "max_turns": 8,
    }
    frozen = runtime.freeze_spec(
        SearchSpec.model_validate(spec_data),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    first = runtime.start_agent_session(run_id, task.candidate_id)
    runtime.bind_agent_handle(
        first.agent_session_id,
        {
            "host": "pi-rpc",
            "external_id": first.agent_session_id,
            "metadata": {
                "timed_out": True,
                "assistant_text": None,
                "progress_handoff": {
                    "status": "timed_out",
                    "summary": "implemented parser skeleton",
                    "workspace": {"dirty": True, "changed_files": ["initial_program.py"]},
                    "verifier": {"count": 0},
                },
            },
        },
    )
    resumed = runtime.redispatch_candidate(
        run_id,
        task.candidate_id,
        {"goal": "finish and verify"},
        worker_budget={"max_runtime_seconds": 120, "max_turns": 8},
    )

    context = runtime.get_agent_context(resumed.agent_session_id)

    assert context["resume"]["is_redispatch"] is True
    assert context["resume"]["latest_handoff"]["summary"] == "implemented parser skeleton"
    assert context["resume"]["previous_sessions"] == [
        {
            "agent_session_id": first.agent_session_id,
            "timed_out": True,
            "runner_failed": False,
            "assistant_summary": None,
            "progress_handoff": {
                "status": "timed_out",
                "summary": "implemented parser skeleton",
                "workspace": {"dirty": True, "changed_files": ["initial_program.py"]},
                "verifier": {"count": 0},
            },
            "error": None,
        }
    ]
    assert context["resume"]["workspace"]["dirty"] is False
    assert resumed.launch["budget_control"]["max_runtime_seconds"] == 120


@pytest.mark.codex
def test_start_agent_session_returns_codex_launch_payload(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, "codex", strategy_name="random", max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    session = runtime.start_agent_session(run_id, task.candidate_id)

    assert session.host == "codex"
    assert session.host_handle.host == "codex"
    assert session.host_handle.task_name == session.launch["task_name"]
    assert session.launch["tool"] == "spawn_agent"
    assert session.launch["agent_type"] == "search_candidate_agent"
    assert session.launch["fork_turns"] == "none"
    assert "agent_session_id=" in session.launch["message"]


@pytest.mark.pi
def test_start_agent_session_returns_pi_rpc_launch_payload(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {
                "max_runtime_seconds": 600,
                "max_turns": 8,
                "on_exceed": "interrupt",
            },
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    session = runtime.start_agent_session(run_id, task.candidate_id)

    assert session.host == "pi-rpc"
    assert session.host_handle.host == "pi-rpc"
    assert session.host_handle.external_id == session.agent_session_id
    assert session.launch["tool"] == "pi_rpc_worker"
    assert session.launch["run_id"] == run_id
    assert session.launch["root"] == str(runtime.root_dir)
    assert session.launch["cwd"] == str(task.workspace)
    assert session.launch["session_id"] == session.agent_session_id
    assert session.launch["budget_control"]["mode"] == "pi_rpc_process_watchdog"
    assert session.launch["budget_control"]["max_runtime_seconds"] == 600
    assert session.launch["budget_control"]["max_turns_hint"] == 8
    assert "search_get_agent_context" in session.launch["prompt"]
    assert str(task.workspace) not in session.launch["prompt"]


@pytest.mark.pi
def test_start_agent_session_accepts_one_dispatch_worker_budget(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {
                "max_runtime_seconds": 600,
                "max_turns": 8,
                "on_exceed": "interrupt",
            },
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    long_session = runtime.start_agent_session(
        run_id,
        task.candidate_id,
        worker_budget={
            "max_runtime_seconds": 1800,
            "max_turns": 40,
            "on_exceed": "interrupt",
        },
    )
    default_session = runtime.start_agent_session(run_id, task.candidate_id)

    assert long_session.launch["budget_control"]["max_runtime_seconds"] == 1800
    assert "assigned_worker_budget={'max_runtime_seconds': 1800" in long_session.launch[
        "prompt"
    ]
    assert default_session.launch["budget_control"]["max_runtime_seconds"] == 600
    reloaded = runtime._load_frozen_spec(frozen.frozen_spec_id)
    assert reloaded.spec.strategy.worker_budget is not None
    assert reloaded.spec.strategy.worker_budget.max_runtime_seconds == 600


@pytest.mark.codex
def test_launch_keeps_candidate_proposal_when_main_directive_is_shared(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(
        project,
        "codex",
        strategy_name="agent_guided",
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(
        run_id,
        plan.plan_id,
        [
            CandidateProposal(
                intent="explore scratch-resident batching",
                hypothesis="reuse scratch slots across dependent operations",
                instructions=["measure one complete batch before interleaving"],
            )
        ],
    )[0]

    session = runtime.start_agent_session(
        run_id,
        task.candidate_id,
        {"goal": "perform deep optimization"},
    )

    assert "candidate_intent: explore scratch-resident batching" in session.launch[
        "message"
    ]
    assert "candidate_hypothesis: reuse scratch slots" in session.launch["message"]
    assert "main_directive.goal: perform deep optimization" in session.launch["message"]


@pytest.mark.codex
def test_redispatch_candidate_overrides_codex_worker_budget(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, "codex", strategy_name="random", max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    first = runtime.start_agent_session(run_id, task.candidate_id)

    redispatched = runtime.redispatch_candidate(
        run_id,
        task.candidate_id,
        "resume after timeout",
        worker_agent_type="search_candidate_agent_deep",
        worker_budget={"max_runtime_seconds": 30, "max_turns": 12, "on_exceed": "interrupt"},
    )

    assert redispatched.agent_session_id != first.agent_session_id
    assert redispatched.launch["agent_type"] == "search_candidate_agent_deep"
    assert redispatched.launch["budget_control"] == {
        "mode": "parent_watchdog",
        "max_runtime_seconds": 30,
        "initial_wait_timeout_ms": 24000,
        "soft_closeout_seconds": 6,
        "closeout_tool": "send_message",
        "closeout_target": redispatched.launch["task_name"],
        "closeout_message": (
            "Worker deadline is approaching. Stop starting new work, run one final "
            "search_run_verifier if needed, write .tmp/handoff.json, and return a concise summary."
        ),
        "final_wait_timeout_ms": 6000,
        "on_exceed": "interrupt",
        "interrupt_tool": "interrupt_agent",
        "interrupt_target": redispatched.launch["task_name"],
        "max_turns_hint": 12,
    }


@pytest.mark.codex
def test_codex_worker_budget_flows_to_watchdog_launch_payload(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "codex",
            "worker_budget": {
                "max_runtime_seconds": 600,
                "max_turns": 8,
                "on_exceed": "interrupt",
            },
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    session = runtime.start_agent_session(run_id, task.candidate_id)

    assert plan.worker_policy["worker_budget"] == {
        "max_runtime_seconds": 600,
        "max_turns": 8,
        "on_exceed": "interrupt",
    }
    assert session.launch["budget_control"] == {
        "mode": "parent_watchdog",
        "max_runtime_seconds": 600,
        "initial_wait_timeout_ms": 555000,
        "soft_closeout_seconds": 45,
        "closeout_tool": "send_message",
        "closeout_target": session.launch["task_name"],
        "closeout_message": (
            "Worker deadline is approaching. Stop starting new work, run one final "
            "search_run_verifier if needed, write .tmp/handoff.json, and return a concise summary."
        ),
        "final_wait_timeout_ms": 45000,
        "on_exceed": "interrupt",
        "interrupt_tool": "interrupt_agent",
        "interrupt_target": session.launch["task_name"],
        "max_turns_hint": 8,
    }


@pytest.mark.codex
def test_codex_worker_launch_options_flow_to_spawn_payload(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "codex",
            "worker_launch": {
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
                "service_tier": "priority",
            },
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    session = runtime.start_agent_session(run_id, task.candidate_id)

    assert plan.worker_policy["worker_launch"] == {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "service_tier": "priority",
    }
    assert session.launch["model"] == "gpt-5.6-terra"
    assert session.launch["reasoning_effort"] == "high"
    assert session.launch["service_tier"] == "priority"


@pytest.mark.pi
def test_pi_rpc_rejects_unsupported_worker_service_tier(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {"max_runtime_seconds": 60},
            "worker_launch": {"service_tier": "priority"},
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    with pytest.raises(ValueError, match="service_tier"):
        runtime.plan_next(run_id, requested_k=1)


def test_start_agent_session_returns_claude_foreground_launch_payload(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, "claude-code", strategy_name="random", max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    session = runtime.start_agent_session(run_id, task.candidate_id)

    assert session.host == "claude-code"
    assert session.host_handle.host == "claude-code"
    assert session.launch["tool"] == "Agent"
    assert session.launch["agent_type"] == "search-candidate-agent"
    assert session.launch["background"] is False
    assert "agent_session_id=" in session.launch["message"]


def test_redispatch_candidate_overrides_claude_tier_and_budget(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, "claude-code", strategy_name="random", max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    redispatched = runtime.redispatch_candidate(
        run_id,
        task.candidate_id,
        {"goal": "resume with deep budget"},
        worker_agent_type="search-candidate-agent-deep",
        worker_budget={"max_turns": 16, "on_exceed": "interrupt"},
    )

    assert redispatched.launch["agent_type"] == "search-candidate-agent-deep"
    assert redispatched.launch["budget_control"] == {
        "mode": "host_turn_limit",
        "max_turns": 16,
        "on_exceed": "interrupt",
    }


def test_redispatch_candidate_rejects_claude_tier_budget_mismatch(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "claude-code",
            "worker_agent_type": "search-candidate-agent-flash",
            "worker_budget": {
                "max_turns": 4,
                "on_exceed": "interrupt",
            },
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    with pytest.raises(ValueError, match="known claude-code worker_agent_type"):
        runtime.redispatch_candidate(
            run_id,
            task.candidate_id,
            {"goal": "resume deeper"},
            worker_agent_type="search-candidate-agent-deep",
        )


def test_claude_worker_budget_flows_to_turn_limit_launch_payload(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "claude-code",
            "worker_agent_type": "search-candidate-agent-deep",
            "worker_budget": {
                "max_turns": 16,
                "on_exceed": "interrupt",
            },
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    session = runtime.start_agent_session(run_id, task.candidate_id)

    assert plan.worker_policy["worker_budget"] == {
        "max_runtime_seconds": None,
        "max_turns": 16,
        "on_exceed": "interrupt",
    }
    assert session.launch["agent_type"] == "search-candidate-agent-deep"
    assert session.launch["budget_control"] == {
        "mode": "host_turn_limit",
        "max_turns": 16,
        "on_exceed": "interrupt",
    }


def test_claude_worker_budget_selects_known_turn_budget_agent(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "claude-code",
            "worker_budget": {
                "max_turns": 4,
                "on_exceed": "interrupt",
            },
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]

    session = runtime.start_agent_session(run_id, task.candidate_id)

    assert plan.worker_policy["worker_agent_type"] == "search-candidate-agent-flash"
    assert session.launch["agent_type"] == "search-candidate-agent-flash"
    assert session.launch["budget_control"]["max_turns"] == 4


def test_claude_worker_budget_rejects_mismatched_known_agent_type(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "claude-code",
            "worker_agent_type": "search-candidate-agent",
            "worker_budget": {
                "max_turns": 16,
                "on_exceed": "interrupt",
            },
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    with pytest.raises(ValueError, match="known claude-code worker_agent_type"):
        runtime.plan_next(run_id, requested_k=1)


def test_host_worker_budget_rejects_unenforceable_limits(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")

    codex_spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "codex",
            "worker_budget": {"max_turns": 8},
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(codex_spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    with pytest.raises(ValueError, match="codex worker_budget requires max_runtime_seconds"):
        runtime.plan_next(run_id, requested_k=1)

    claude_runtime = FileSearchRuntime(tmp_path / ".search-claude")
    claude_spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "claude-code",
            "worker_budget": {"max_runtime_seconds": 600},
        },
        max_candidates=1,
    )
    frozen = claude_runtime.freeze_spec(claude_spec, [project / "evaluator.py"])
    run_id = claude_runtime.create_run(frozen.frozen_spec_id)
    with pytest.raises(ValueError, match="claude-code worker_budget requires max_turns"):
        claude_runtime.plan_next(run_id, requested_k=1)

    pi_runtime = FileSearchRuntime(tmp_path / ".search-pi")
    pi_spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {"max_turns": 8},
        },
        max_candidates=1,
    )
    frozen = pi_runtime.freeze_spec(pi_spec, [project / "evaluator.py"])
    run_id = pi_runtime.create_run(frozen.frozen_spec_id)
    with pytest.raises(ValueError, match="pi-rpc worker_budget requires max_runtime_seconds"):
        pi_runtime.plan_next(run_id, requested_k=1)


@pytest.mark.codex
def test_bind_agent_handle_records_codex_task_name(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, "codex", strategy_name="random", max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)

    updated = runtime.bind_agent_handle(
        session.agent_session_id,
        {"host": "codex", "task_name": "search_agent_0001", "nickname": "search worker"},
    )

    assert updated.host == "codex"
    assert updated.host_handle.task_name == "search_agent_0001"
    assert updated.host_handle.nickname == "search worker"
    assert updated.opencode_session_id is None


@pytest.mark.codex
def test_codex_continue_agent_session_uses_bound_worker_and_budget(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, "codex", strategy_name="random", max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)
    runtime.bind_agent_handle(
        session.agent_session_id,
        {"host": "codex", "task_name": "search_agent_0001"},
    )

    continued = runtime.continue_agent_session(
        session.agent_session_id,
        {"goal": "continue"},
        worker_budget={"max_runtime_seconds": 900, "on_exceed": "interrupt"},
    )

    assert continued.launch["tool"] == "followup_task"
    assert continued.launch["target"] == "search_agent_0001"
    assert continued.launch["budget_control"]["max_runtime_seconds"] == 900


def test_claude_continue_agent_session_uses_send_message_payload(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, "claude-code", strategy_name="random", max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)
    runtime.bind_agent_handle(
        session.agent_session_id,
        {"host": "claude-code", "external_id": "agent_123"},
    )

    continued = runtime.continue_agent_session(
        session.agent_session_id,
        {"goal": "continue"},
    )

    assert continued.launch["tool"] == "SendMessage"
    assert continued.launch["agent"] == "agent_123"
    assert "continue_existing_agent_session=true" in continued.launch["message"]


@pytest.mark.pi
def test_pi_rpc_continue_agent_session_requires_redispatch(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {
                "max_runtime_seconds": 600,
                "on_exceed": "interrupt",
            },
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)
    runtime.bind_agent_handle(
        session.agent_session_id,
        {
            "host": "pi-rpc",
            "external_id": session.agent_session_id,
            "metadata": {"event_log": "/tmp/pi-rpc-agent_0001.jsonl"},
        },
    )

    with pytest.raises(RuntimeError, match="search_redispatch_candidate"):
        runtime.continue_agent_session(
            session.agent_session_id,
            {"goal": "continue"},
        )

    report = runtime.report(run_id).read_text(encoding="utf-8")
    assert "| Session | Host | Candidate | Verifier Runs |" in report
    assert "| Session | Host | Handle | Candidate | Verifier Runs |" not in report


def test_bind_and_continue_agent_session_reuses_existing_opencode_session(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "SearchCandidateAgent",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(
        run_id,
        task.candidate_id,
        {"goal": "try one concrete variant"},
    )

    bound = runtime.bind_opencode_session(
        session.agent_session_id,
        " opencode_session_001 ",
    )
    assert bound.opencode_session_id == "opencode_session_001"

    repeated = runtime.bind_opencode_session(
        session.agent_session_id,
        "opencode_session_001",
    )
    assert repeated.opencode_session_id == "opencode_session_001"

    continued = runtime.continue_agent_session(
        session.agent_session_id,
        {"goal": "keep improving the same node"},
    )

    assert continued.agent_session_id == session.agent_session_id
    assert continued.candidate_id == task.candidate_id
    assert continued.workspace == task.workspace
    assert continued.opencode_session_id == "opencode_session_001"
    assert continued.directive == {"goal": "keep improving the same node"}
    assert continued.launch["task_id"] == "opencode_session_001"
    assert continued.launch["subagent_type"] == "SearchCandidateAgent"
    assert "required" not in continued.launch
    assert continued.agent_session_id in continued.launch["prompt"]
    assert task.candidate_id in continued.launch["prompt"]
    assert "search_get_agent_context" in continued.launch["prompt"]
    assert str(task.workspace) not in continued.launch["prompt"]

    context = runtime.get_agent_context(session.agent_session_id)
    assert context["candidate_id"] == task.candidate_id
    assert context["workspace"] == str(task.workspace)

    history = runtime.list_history(run_id)
    candidate = history["candidates"][0]
    assert candidate["agent_sessions"][0]["opencode_session_id"] == "opencode_session_001"

    report = runtime.report(run_id).read_text(encoding="utf-8")
    assert "| Session | Host | Handle | Candidate | Verifier Runs |" in report
    assert "Handle / OpenCode Session" not in report
    assert "opencode_session_001" in report

    with pytest.raises(ValueError, match="different OpenCode session"):
        runtime.bind_opencode_session(session.agent_session_id, "opencode_session_002")


def test_continue_agent_session_requires_bound_opencode_session(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)

    with pytest.raises(RuntimeError, match="no bound OpenCode session id"):
        runtime.continue_agent_session(session.agent_session_id)


def test_plan_next_caps_batch_size_to_max_parallel(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec_data = spec_for(project, max_candidates=4).model_dump(mode="json")
    spec_data["budget"]["max_parallel"] = 2
    frozen = runtime.freeze_spec(SearchSpec.model_validate(spec_data), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=4)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    assert plan.requested_k == 4
    assert plan.planned_k == 2
    assert [task.candidate_id for task in tasks] == ["c001", "c002"]

    next_plan = runtime.plan_next(run_id, requested_k=4)
    next_tasks = runtime.start_batch(run_id, next_plan.plan_id)

    assert next_plan.planned_k == 2
    assert [task.candidate_id for task in next_tasks] == ["c003", "c004"]


def test_start_agent_session_does_not_enforce_active_pool_status(tmp_path: Path) -> None:
    """max_parallel sizes batches; the runtime does not supervise live workers."""
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec_data = spec_for(project, max_candidates=2).model_dump(mode="json")
    spec_data["budget"]["max_parallel"] = 1
    frozen = runtime.freeze_spec(SearchSpec.model_validate(spec_data), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    first_plan = runtime.plan_next(run_id, requested_k=2)
    first_task = runtime.start_batch(run_id, first_plan.plan_id)[0]
    second_plan = runtime.plan_next(run_id, requested_k=2)
    second_task = runtime.start_batch(run_id, second_plan.plan_id)[0]

    first = runtime.start_agent_session(
        run_id, first_task.candidate_id, {"goal": "first"},
    )
    second = runtime.start_agent_session(
        run_id, second_task.candidate_id, {"goal": "second"},
    )

    assert first.candidate_id == first_task.candidate_id
    assert second.candidate_id == second_task.candidate_id


def test_start_agent_session_allocates_unique_ids_under_parallel_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec_data = spec_for(project, max_candidates=2).model_dump(mode="json")
    spec_data["budget"]["max_parallel"] = 2
    frozen = runtime.freeze_spec(SearchSpec.model_validate(spec_data), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    original_load_run = runtime._load_run
    loaded_count = 0
    loaded_lock = threading.Lock()
    second_loaded = threading.Event()

    def load_run_with_overlap(load_run_id: str):
        nonlocal loaded_count
        run = original_load_run(load_run_id)
        if load_run_id == run_id:
            with loaded_lock:
                loaded_count += 1
                current_count = loaded_count
                if loaded_count == 2:
                    second_loaded.set()
            if current_count == 1:
                second_loaded.wait(timeout=0.25)
        return run

    monkeypatch.setattr(runtime, "_load_run", load_run_with_overlap)
    start_barrier = threading.Barrier(2)

    def start(candidate_id: str):
        start_barrier.wait(timeout=5)
        return runtime.start_agent_session(run_id, candidate_id, {"goal": candidate_id})

    with ThreadPoolExecutor(max_workers=2) as pool:
        sessions = list(pool.map(start, [task.candidate_id for task in tasks]))

    assert sorted(session.agent_session_id for session in sessions) == [
        FileSearchRuntime._make_agent_session_id(run_id, 1),
        FileSearchRuntime._make_agent_session_id(run_id, 2),
    ]
    assert sorted(session.candidate_id for session in sessions) == ["c001", "c002"]
    assert sorted(session.agent_session_id for session in runtime._load_agent_sessions(run_id)) == [
        FileSearchRuntime._make_agent_session_id(run_id, 1),
        FileSearchRuntime._make_agent_session_id(run_id, 2),
    ]
    assert original_load_run(run_id).next_agent_session_index == 3


def test_get_agent_context_has_only_authoritative_worker_fields(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
        },
        max_candidates=2,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    session = runtime.start_agent_session(run_id, tasks[0].candidate_id, {"goal": "iterate"})

    context = runtime.get_agent_context(session.agent_session_id)
    for forbidden in (
        "status",
        "phase",
        "visibility_mode",
        "budget",
        "peer_status",
        "observations",
    ):
        assert forbidden not in context, f"get_agent_context must not return {forbidden}"
    assert context["candidate_task"]["candidate_id"] == tasks[0].candidate_id
    assert "history" in context
    assert "iterations" in context


def test_agent_session_ids_are_unique_across_runs(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])

    first_run_id = runtime.create_run(frozen.frozen_spec_id)
    second_run_id = runtime.create_run(frozen.frozen_spec_id)
    first_plan = runtime.plan_next(first_run_id, requested_k=1)
    first_task = runtime.start_batch(first_run_id, first_plan.plan_id)[0]
    second_plan = runtime.plan_next(second_run_id, requested_k=1)
    second_task = runtime.start_batch(second_run_id, second_plan.plan_id)[0]

    first = runtime.start_agent_session(first_run_id, first_task.candidate_id)
    second = runtime.start_agent_session(second_run_id, second_task.candidate_id)

    assert first.agent_session_id != second.agent_session_id
    assert first_run_id.removeprefix("run_") in first.agent_session_id
    assert second_run_id.removeprefix("run_") in second.agent_session_id
    assert runtime.get_agent_context(first.agent_session_id)["run_id"] == first_run_id
    assert runtime.get_agent_context(second.agent_session_id)["run_id"] == second_run_id


def test_legacy_agent_session_id_collision_is_not_silent(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])

    first_run_id = runtime.create_run(frozen.frozen_spec_id)
    second_run_id = runtime.create_run(frozen.frozen_spec_id)
    first_plan = runtime.plan_next(first_run_id, requested_k=1)
    second_plan = runtime.plan_next(second_run_id, requested_k=1)
    first_task = runtime.start_batch(first_run_id, first_plan.plan_id)[0]
    second_task = runtime.start_batch(second_run_id, second_plan.plan_id)[0]
    first = runtime.start_agent_session(first_run_id, first_task.candidate_id)
    second = runtime.start_agent_session(second_run_id, second_task.candidate_id)

    legacy_first = first.model_copy(update={"agent_session_id": "agent_001"})
    legacy_second = second.model_copy(update={"agent_session_id": "agent_001"})
    runtime._write_agent_session(legacy_first)
    runtime._write_agent_session(legacy_second)

    with pytest.raises(RuntimeError, match="ambiguous agent_session_id"):
        runtime.get_agent_context("agent_001")


def test_agent_guided_strategy_requires_and_validates_proposals(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "agent_guided",
            "history_policy": {"scope": "top_n", "top_n": 2},
        },
        max_candidates=3,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan1 = runtime.plan_next(run_id, requested_k=1)
    assert plan1.requires_agent_proposals is True
    with pytest.raises(ValueError):
        runtime.start_batch(run_id, plan1.plan_id)

    first_tasks = runtime.start_batch(
        run_id,
        plan1.plan_id,
        [CandidateProposal(intent="bootstrap from source")],
    )
    assert first_tasks[0].base_candidate_id is None

    plan2 = runtime.plan_next(run_id, requested_k=1)
    assert plan2.proposal_contract.must_reference_one_of == ["c001"]  # type: ignore[union-attr]
    assert any(
        "deepen_incumbent, transfer_feature, and macro_restart" in note
        for note in plan2.proposal_contract.notes  # type: ignore[union-attr]
    )
    with pytest.raises(ValueError):
        runtime.start_batch(
            run_id,
            plan2.plan_id,
            [CandidateProposal(intent="invalid proposal", parent_candidate_ids=["missing"])],
        )
    with pytest.raises(ValueError):
        runtime.start_batch(
            run_id,
            plan2.plan_id,
            [
                CandidateProposal(intent="valid but too many 1", parent_candidate_ids=["c001"]),
                CandidateProposal(intent="valid but too many 2", parent_candidate_ids=["c001"]),
            ],
        )

    valid_tasks = runtime.start_batch(
        run_id,
        plan2.plan_id,
        [
            CandidateProposal(
                parent_candidate_ids=["c001"],
                base_candidate_id="c001",
                intent="derive from first candidate",
                expected_tradeoff="reuse the first candidate as base",
            )
        ],
    )

    assert valid_tasks[0].candidate_id == "c002"
    assert valid_tasks[0].base_candidate_id == "c001"
    assert valid_tasks[0].parent_candidate_ids == ["c001"]


def test_evolve_strategy_derives_followup_from_best_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(project, {"name": "evolve"}, max_candidates=4)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, 2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    (tasks[0].workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tasks[1].workspace / "initial_program.py").write_text("VALUE = 2\n", encoding="utf-8")

    for task in tasks:
        runtime.start_agent_session(run_id, task.candidate_id, {"goal": "submit"})

    def fake_run(*args, **kwargs):
        cwd = Path(kwargs["cwd"])
        score = 0.9 if cwd.name == "c002" else 0.1
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f'{{"combined_score": {score}}}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, "c001")
    runtime.run_verifier(run_id, "c002")

    plan = runtime.plan_next(run_id, 2)
    followups = runtime.start_batch(run_id, plan.plan_id)

    assert plan.strategy_trace["parent_candidate_id"] == "c002"
    assert followups[0].base_candidate_id == "c002"
    assert followups[0].parent_candidate_ids == ["c002"]
    assert (followups[0].workspace / "initial_program.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_git_worktree_runtime_branches_followup_from_best_parent_commit(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "namespace = {}\n"
        "exec(open('initial_program.py', encoding='utf-8').read(), namespace)\n"
        "print(json.dumps({'combined_score': float(namespace['VALUE'])}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec_data = spec_with_strategy(
        project,
        {"name": "evolve"},
        max_candidates=3,
    ).model_dump(mode="json")
    spec_data["workspace"] = {"backend": "git_worktree"}
    spec = SearchSpec.model_validate(spec_data)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    first_plan = runtime.plan_next(run_id, 2)
    first = runtime.start_batch(run_id, first_plan.plan_id)
    (first[0].workspace / "initial_program.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (first[1].workspace / "initial_program.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    runtime.run_verifier(run_id, "c001")
    runtime.run_verifier(run_id, "c002")

    parent = runtime._load_candidate_record(run_id, "c002")
    best_parent_iteration = runtime._best_iteration_record(parent, "maximize")
    assert best_parent_iteration is not None
    assert best_parent_iteration.git_head is not None

    second_plan = runtime.plan_next(run_id, 1)
    child = runtime.start_batch(run_id, second_plan.plan_id)[0]

    common_dirs = {
        subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=task.workspace,
            text=True,
        ).strip()
        for task in (*first, child)
    }
    assert len(common_dirs) == 1
    assert [task.workspace_backend for task in first] == [
        "git_worktree",
        "git_worktree",
    ]
    assert first[0].workspace_branch != first[1].workspace_branch
    assert second_plan.strategy_trace["parent_candidate_id"] == "c002"
    assert child.base_candidate_id == "c002"
    assert child.workspace_backend == "git_worktree"
    assert child.workspace_branch == f"gp/{run_id}/c003"
    assert child.workspace_base_revision == best_parent_iteration.git_head
    child_record = runtime._load_candidate_record(run_id, child.candidate_id)
    child_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=child.workspace, text=True
    ).strip()
    assert child_head == child_record.results_ledger_git_head
    assert child_head != best_parent_iteration.git_head
    assert child.workspace.joinpath("results.tsv").read_text(encoding="utf-8") == (
        parent.task.workspace.joinpath("results.tsv").read_text(encoding="utf-8")
    )
    assert (child.workspace / "initial_program.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 2\n"


def test_git_worktree_start_batch_recovers_after_materialization_before_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec_data = spec_with_strategy(
        project,
        {"name": "independent_branches"},
        max_candidates=1,
    ).model_dump(mode="json")
    spec_data["workspace"] = {"backend": "git_worktree"}
    spec = SearchSpec.model_validate(spec_data)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, 1)
    original_write = runtime._write_candidate_record
    failed = False

    def fail_once(run_id_arg: str, record: CandidateRecord) -> None:
        nonlocal failed
        if not failed:
            failed = True
            write_json(
                runtime._candidate_dir(run_id_arg, record.candidate_id)
                / "candidate.json",
                record.model_dump(mode="json"),
            )
            raise RuntimeError("simulated state write failure")
        original_write(run_id_arg, record)

    monkeypatch.setattr(runtime, "_write_candidate_record", fail_once)

    with pytest.raises(RuntimeError, match="simulated state write failure"):
        runtime.start_batch(run_id, plan.plan_id)

    tasks = runtime.start_batch(run_id, plan.plan_id)
    assert [task.candidate_id for task in tasks] == ["c001"]
    assert runtime.status(run_id).candidates_total == 1
    assert (
        runtime._candidate_dir(run_id, "c001") / "task.json"
    ).is_file()


def test_start_batch_is_serialized_and_idempotent_for_same_plan(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec_data = spec_with_strategy(
        project,
        {"name": "independent_branches"},
        max_candidates=2,
    ).model_dump(mode="json")
    spec_data["workspace"] = {"backend": "git_worktree"}
    spec = SearchSpec.model_validate(spec_data)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, 2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(runtime.start_batch, run_id, plan.plan_id)
            for _ in range(2)
        ]
        batches = [future.result() for future in futures]

    assert [[task.candidate_id for task in batch] for batch in batches] == [
        ["c001", "c002"],
        ["c001", "c002"],
    ]
    assert runtime.status(run_id).candidates_total == 2


def test_git_worktree_followup_rejects_parent_without_clean_verifier_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "namespace = {}\n"
        "exec(open('initial_program.py', encoding='utf-8').read(), namespace)\n"
        "print(json.dumps({'combined_score': float(namespace['VALUE'])}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec_data = spec_with_strategy(
        project,
        {"name": "evolve"},
        max_candidates=2,
    ).model_dump(mode="json")
    spec_data["workspace"] = {"backend": "git_worktree"}
    spec = SearchSpec.model_validate(spec_data)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    first_plan = runtime.plan_next(run_id, 1)
    parent = runtime.start_batch(run_id, first_plan.plan_id)[0]
    (parent.workspace / "initial_program.py").write_text(
        "VALUE = 9\n", encoding="utf-8"
    )
    monkeypatch.setattr(runtime, "_commit_workspace_iteration", lambda *args: None)
    report = runtime.run_verifier(run_id, parent.candidate_id)
    assert report.aggregate_score == 9.0

    second_plan = runtime.plan_next(run_id, 1)
    with pytest.raises(RuntimeError, match="no clean verifier-backed Git iteration"):
        runtime.start_batch(run_id, second_plan.plan_id)


def test_evolve_planning_keeps_candidate_with_valid_iteration_after_latest_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        spec_with_strategy(
            project,
            {"name": "evolve", "history_policy": {"scope": "top_n", "top_n": 2}},
            max_candidates=3,
        ),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    first_plan = runtime.plan_next(run_id, requested_k=2)
    first_tasks = runtime.start_batch(run_id, first_plan.plan_id)

    scores = {"c001": [0.9, 0.0], "c002": [0.8]}
    real_run = subprocess.run

    def fake_run(*args, **kwargs):
        command = args[0]
        if command and command[0] != "python":
            return real_run(*args, **kwargs)
        candidate_id = Path(kwargs["cwd"]).name
        score = scores[candidate_id].pop(0)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0 if score > 0 else 1,
            stdout=f'{{"combined_score": {score}}}\n' if score > 0 else "",
            stderr="verifier failed" if score == 0 else "",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)

    runtime.run_verifier(run_id, first_tasks[0].candidate_id)
    runtime.run_verifier(run_id, first_tasks[0].candidate_id)
    runtime.run_verifier(run_id, first_tasks[1].candidate_id)

    history = runtime.list_history(run_id, top_n=2)
    second_plan = runtime.plan_next(run_id, requested_k=1)

    assert history["candidates"][0]["candidate_id"] == "c001"
    assert history["candidates"][0]["score"] == 0.9
    assert history["candidates"][0]["latest_score"] == 0.0
    assert history["candidates"][0]["latest_process_passed"] is False
    assert history["candidates"][0]["latest_failure_classes"] == [
        "VerifierCommandFailed"
    ]
    assert second_plan.strategy_trace["parent_candidate_id"] == "c001"
    assert second_plan.official_history["candidates"][0]["score"] == 0.9


def test_openevolve_strategy_bootstraps_from_source_with_openevolve_trace(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(project, {"name": "openevolve"}, max_candidates=2)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan = runtime.plan_next(run_id, 2)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    assert plan.requires_agent_proposals is False
    assert plan.strategy_trace["selection_rule"] == "openevolve bootstrap"
    assert plan.strategy_trace["sampling_mode"] == "bootstrap"
    assert plan.derivation_policy["base_workspace_source"] == "source"
    assert [task.base_candidate_id for task in tasks] == [None, None]


def test_openevolve_strategy_samples_exploration_parent_and_inspirations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "openevolve",
            "config": {
                "seed": 1,
                "exploration_ratio": 1.0,
                "exploitation_ratio": 0.0,
                "archive_size": 1,
                "num_inspirations": 2,
            },
        },
        max_candidates=3,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    first_plan = runtime.plan_next(run_id, 2)
    first_tasks = runtime.start_batch(run_id, first_plan.plan_id)
    (first_tasks[0].workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    (first_tasks[1].workspace / "initial_program.py").write_text("VALUE = 2\n", encoding="utf-8")

    for task in first_tasks:
        runtime.start_agent_session(run_id, task.candidate_id, {"goal": "score parent pool"})

    def fake_run(*args, **kwargs):
        cwd = Path(kwargs["cwd"])
        score = 0.9 if cwd.name == "c002" else 0.1
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f'{{"combined_score": {score}}}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, "c001")
    runtime.run_verifier(run_id, "c002")

    plan = runtime.plan_next(run_id, 1)
    followups = runtime.start_batch(run_id, plan.plan_id)

    assert plan.strategy_trace["selection_rule"] == "openevolve sampled parent plus inspirations"
    assert plan.strategy_trace["sampling_mode"] == "exploration"
    assert plan.strategy_trace["parent_candidate_id"] == "c001"
    assert plan.strategy_trace["archive_candidate_ids"] == ["c002"]
    assert plan.strategy_trace["inspiration_candidate_ids"] == ["c002"]
    assert plan.work_orders[0].base_candidate_id == "c001"
    assert "OpenEvolve sampled parent" in plan.work_orders[0].instructions[0]
    assert followups[0].base_candidate_id == "c001"
    assert (followups[0].workspace / "initial_program.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_random_strategy_gen1_independent_bootstrap(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(project, {"name": "random"}, max_candidates=4)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan = runtime.plan_next(run_id, 2)

    assert plan.requires_agent_proposals is False
    assert plan.strategy_trace["selection_rule"] == "random bootstrap"
    assert "parent_candidate_id" not in plan.strategy_trace
    assert plan.derivation_policy["base_workspace_source"] == "source"
    assert len(plan.work_orders) == 2
    assert all(wo.base_candidate_id is None for wo in plan.work_orders)

    tasks = runtime.start_batch(run_id, plan.plan_id)
    assert all(t.base_candidate_id is None for t in tasks)


def test_random_strategy_gen2_picks_scored_parent_with_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {"name": "random", "config": {"seed": 42}},
        max_candidates=4,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, 2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    (tasks[0].workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tasks[1].workspace / "initial_program.py").write_text("VALUE = 2\n", encoding="utf-8")

    for task in tasks:
        runtime.start_agent_session(run_id, task.candidate_id, {"goal": "submit"})

    def fake_run(*args, **kwargs):
        cwd = Path(kwargs["cwd"])
        score = 0.9 if cwd.name == "c002" else 0.1
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f'{{"combined_score": {score}}}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, "c001")
    runtime.run_verifier(run_id, "c002")

    plan = runtime.plan_next(run_id, 2)
    followups = runtime.start_batch(run_id, plan.plan_id)

    parent_id = plan.strategy_trace["parent_candidate_id"]
    assert plan.strategy_trace["selection_rule"] == "random verified parent"
    assert parent_id in {"c001", "c002"}
    assert plan.strategy_trace["seed"] == 42
    assert followups[0].base_candidate_id == parent_id
    assert followups[0].parent_candidate_ids == [parent_id]
    expected_value = "VALUE = 2\n" if parent_id == "c002" else "VALUE = 1\n"
    assert (followups[0].workspace / "initial_program.py").read_text(encoding="utf-8") == expected_value


def test_random_strategy_gen2_without_seed_picks_scored_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(project, {"name": "random"}, max_candidates=4)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, 2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    (tasks[0].workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tasks[1].workspace / "initial_program.py").write_text("VALUE = 2\n", encoding="utf-8")

    for task in tasks:
        runtime.start_agent_session(run_id, task.candidate_id, {"goal": "submit"})

    def fake_run(*args, **kwargs):
        cwd = Path(kwargs["cwd"])
        score = 0.9 if cwd.name == "c002" else 0.1
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f'{{"combined_score": {score}}}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, "c001")
    runtime.run_verifier(run_id, "c002")

    plan = runtime.plan_next(run_id, 2)
    followups = runtime.start_batch(run_id, plan.plan_id)

    parent_id = plan.strategy_trace["parent_candidate_id"]
    assert plan.strategy_trace["selection_rule"] == "random verified parent"
    assert parent_id in {"c001", "c002"}
    assert plan.strategy_trace["seed"] is None
    assert followups[0].base_candidate_id == parent_id
    assert followups[0].parent_candidate_ids == [parent_id]


def test_random_strategy_name_normalizes_case_and_dash(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")

    for name in ("Random", "random-mode", "RANDOM_MODE"):
        spec = spec_with_strategy(project, {"name": name}, max_candidates=4)
        frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
        run_id = runtime.create_run(frozen.frozen_spec_id)

        plan = runtime.plan_next(run_id, 2)

        assert plan.strategy_trace["selection_rule"] == "random bootstrap"
        assert plan.requires_agent_proposals is False


@pytest.mark.parametrize(
    ("host", "strategy_name", "requires_proposals", "expected_launch"),
    [
        (
            "opencode",
            "agent_guided",
            True,
            {"subagent_type": "SearchCandidateAgent"},
        ),
        (
            "opencode",
            "random",
            False,
            {"subagent_type": "SearchCandidateAgent"},
        ),
        (
            "codex",
            "default",
            True,
            {"tool": "spawn_agent", "agent_type": "search_candidate_agent"},
        ),
        (
            "codex",
            "random-mode",
            False,
            {"tool": "spawn_agent", "agent_type": "search_candidate_agent"},
        ),
        (
            "claude-code",
            "agent",
            True,
            {
                "tool": "Agent",
                "agent_type": "search-candidate-agent",
                "background": False,
            },
        ),
        (
            "claude-code",
            "random_mode",
            False,
            {
                "tool": "Agent",
                "agent_type": "search-candidate-agent",
                "background": False,
            },
        ),
    ],
)
def test_all_hosts_create_sessions_for_portable_strategy_modes(
    tmp_path: Path,
    host: str,
    expected_launch: dict[str, object],
    strategy_name: str,
    requires_proposals: bool,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, host, strategy_name=strategy_name, max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan = runtime.plan_next(run_id, requested_k=1)

    assert plan.worker_policy["host"] == host
    assert plan.requires_agent_proposals is requires_proposals
    if requires_proposals:
        tasks = runtime.start_batch(
            run_id,
            plan.plan_id,
            [CandidateProposal(intent=f"{host} {strategy_name} candidate")],
        )
    else:
        tasks = runtime.start_batch(run_id, plan.plan_id)

    session = runtime.start_agent_session(run_id, tasks[0].candidate_id)

    assert session.host == host
    assert session.host_handle.host == host
    assert session.agent_session_id in (
        session.launch.get("prompt") or session.launch.get("message")
    )
    for key, value in expected_launch.items():
        assert session.launch[key] == value


@pytest.mark.parametrize(
    "strategy_name",
    ["evolve", "openevolve", "mcts"],
)
def test_opencode_accepts_existing_builtin_strategy_modes(
    tmp_path: Path,
    strategy_name: str,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, "opencode", strategy_name=strategy_name, max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan = runtime.plan_next(run_id, requested_k=1)

    assert plan.worker_policy["host"] == "opencode"
    assert plan.strategy.name == strategy_name


@pytest.mark.parametrize(
    ("host", "strategy_name"),
    [("codex", "independent_branches"), ("claude-code", "openevolve")],
)
def test_non_opencode_hosts_reject_non_portable_strategies(
    tmp_path: Path,
    host: str,
    strategy_name: str,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_host(project, host, strategy_name=strategy_name, max_candidates=1)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    with pytest.raises(ValueError, match=f"{host}.*{strategy_name}"):
        runtime.plan_next(run_id, requested_k=1)


@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_non_opencode_hosts_reject_non_builtin_strategy_drivers(
    tmp_path: Path,
    host: str,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "driver": "python",
            "ref": "goal_plus.strategies.adaptevolve:AdaptEvolveStrategy",
            "worker_mode": "agent-session-pool",
            "worker_host": host,
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    with pytest.raises(ValueError, match=f"{host}.*only supports builtin"):
        runtime.plan_next(run_id, requested_k=1)


def test_python_strategy_driver_can_return_standard_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    strategy_module = tmp_path / "custom_strategy.py"
    strategy_module.write_text(
        "class Strategy:\n"
        "    def __init__(self, config):\n"
        "        self.config = config\n"
        "    def plan_next(self, payload):\n"
        "        return {\n"
        "            'requires_agent_proposals': True,\n"
        "            'official_history': payload['history'],\n"
        "            'proposal_contract': {'count': payload['planned_k']},\n"
        "            'strategy_trace': {'custom': self.config.get('label')},\n"
        "        }\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "custom_agent",
            "driver": "python",
            "ref": "custom_strategy:Strategy",
            "config": {"label": "unit"},
        },
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan = runtime.plan_next(run_id, 2)

    assert plan.requires_agent_proposals is True
    assert plan.strategy_trace["custom"] == "unit"
    assert plan.proposal_contract.count == 2  # type: ignore[union-attr]


def test_python_strategy_worker_policy_controls_launch_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    strategy_module = tmp_path / "dynamic_worker_strategy.py"
    strategy_module.write_text(
        "class Strategy:\n"
        "    def __init__(self, config):\n"
        "        self.config = config\n"
        "    def plan_next(self, payload):\n"
        "        return {\n"
        "            'requires_agent_proposals': False,\n"
        "            'worker_policy': {\n"
        "                'mode': 'agent-session-pool',\n"
        "                'subagent_type': 'SearchCandidateAgentDeep',\n"
        "                'requires_agent_session': True,\n"
        "            },\n"
        "            'work_orders': [\n"
        "                {\n"
        "                    'slot': 1,\n"
        "                    'intent': 'dynamic deep worker',\n"
        "                    'hypothesis': 'dynamic deep worker',\n"
        "                    'metadata': {'selected_worker_agent_type': 'SearchCandidateAgentDeep'},\n"
        "                }\n"
        "            ],\n"
        "            'strategy_trace': {'selected_worker_agent_type': 'SearchCandidateAgentDeep'},\n"
        "        }\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "dynamic_worker",
            "driver": "python",
            "ref": "dynamic_worker_strategy:Strategy",
            "worker_agent_type": "SearchCandidateAgentFlash",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan = runtime.plan_next(run_id, 1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    session = runtime.start_agent_session(run_id, tasks[0].candidate_id)

    assert plan.worker_policy["subagent_type"] == "SearchCandidateAgentDeep"
    assert tasks[0].strategy_metadata["worker_policy"]["subagent_type"] == "SearchCandidateAgentDeep"
    assert session.launch["subagent_type"] == "SearchCandidateAgentDeep"


def test_adaptevolve_bootstraps_with_flash_then_escalates_after_low_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "adaptevolve",
            "driver": "python",
            "ref": "goal_plus.strategies.adaptevolve:AdaptEvolveStrategy",
            "worker_agent_type": "SearchCandidateAgent",
            "config": {
                "tiers": [
                    "SearchCandidateAgentFlash",
                    "SearchCandidateAgentDeep",
                    "SearchCandidateAgentExtraDeep",
                ],
                "low_score_threshold": 0.2,
                "high_score_threshold": 0.8,
            },
        },
        max_candidates=2,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan1 = runtime.plan_next(run_id, 1)
    first_tasks = runtime.start_batch(run_id, plan1.plan_id)
    first_session = runtime.start_agent_session(run_id, first_tasks[0].candidate_id)

    assert plan1.strategy_trace["selection_rule"] == "adaptevolve bootstrap"
    assert plan1.strategy_trace["selected_worker_agent_type"] == "SearchCandidateAgentFlash"
    assert plan1.worker_policy["subagent_type"] == "SearchCandidateAgentFlash"
    assert first_session.launch["subagent_type"] == "SearchCandidateAgentFlash"

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"combined_score": 0.05}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    runtime.run_verifier(
        run_id,
        first_tasks[0].candidate_id,
        agent_session_id=first_session.agent_session_id,
    )

    plan2 = runtime.plan_next(run_id, 1)
    followups = runtime.start_batch(run_id, plan2.plan_id)
    followup_session = runtime.start_agent_session(run_id, followups[0].candidate_id)

    assert plan2.strategy_trace["selection_rule"] == "adaptevolve mutate best parent"
    assert plan2.strategy_trace["parent_candidate_id"] == first_tasks[0].candidate_id
    assert plan2.strategy_trace["selected_worker_agent_type"] == "SearchCandidateAgentDeep"
    assert plan2.worker_policy["subagent_type"] == "SearchCandidateAgentDeep"
    assert followups[0].base_candidate_id == first_tasks[0].candidate_id
    assert followups[0].strategy_metadata["worker_policy"]["subagent_type"] == "SearchCandidateAgentDeep"
    assert followup_session.launch["subagent_type"] == "SearchCandidateAgentDeep"


def test_run_verifier_records_edit_surface_violation_in_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    candidate_id = tasks[0].candidate_id
    session = runtime.start_agent_session(run_id, candidate_id, {"goal": "cheat"})

    # Worker touches a denied file.
    (tasks[0].workspace / "config.yaml").write_text("name: tampered\n", encoding="utf-8")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"combined_score": 0.9, "valid": true}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, candidate_id, agent_session_id=session.agent_session_id)

    record = runtime._load_candidate_record(run_id, candidate_id)
    it = record.iterations[-1]
    assert it.touched_denied_files is True
    assert "config.yaml" in it.changed_files


def test_run_verifier_reports_and_cleans_verifier_workspace_side_effect(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "if os.environ['GOAL_PLUS_VERIFIER_PHASE'] == 'candidate':\n"
        "    output = Path('.goal-plus-verifiers/generated.bin')\n"
        "    output.parent.mkdir(parents=True, exist_ok=True)\n"
        "    output.write_text('compiled', encoding='utf-8')\n"
        "print(json.dumps({'combined_score': 1.0}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".search")
    run_id, candidate_id, workspace = create_candidate(runtime, project)
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")

    report = runtime.run_verifier(run_id, candidate_id)

    result = report.verifier_results[0]
    assert report.process_passed is False
    assert result.failure_class == "VerifierWorkspaceSideEffect"
    assert result.metrics["verifier_workspace_side_effects"] == [
        ".goal-plus-verifiers/generated.bin"
    ]
    assert result.metrics["cleanup_failures"] == []
    assert result.metrics["infrastructure_failure"] is True
    assert result.metrics["candidate_action"] == "stop_and_report"
    assert not (workspace / ".goal-plus-verifiers/generated.bin").exists()
    assert (workspace / "initial_program.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_run_verifier_classifies_legacy_generated_verifier_file_as_infrastructure(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    run_id, candidate_id, workspace = create_candidate(runtime, project)
    generated = workspace / ".goal-plus-verifiers/generated.bin"
    generated.parent.mkdir(parents=True)
    generated.write_text("legacy side effect", encoding="utf-8")

    report = runtime.run_verifier(run_id, candidate_id)

    result = report.verifier_results[0]
    assert report.process_passed is False
    assert result.failure_class == "VerifierWorkspaceSideEffect"
    assert result.metrics["infrastructure_failure"] is True
    assert result.metrics["candidate_action"] == "stop_and_report"
    assert runtime._git_status(workspace) == [
        "?? .goal-plus-verifiers/generated.bin"
    ]


def test_run_verifier_records_failure_class_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    candidate_id = tasks[0].candidate_id
    session = runtime.start_agent_session(run_id, candidate_id, {"goal": "iterate"})

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    report = runtime.run_verifier(
        run_id, candidate_id, agent_session_id=session.agent_session_id
    )

    assert report.aggregate_score == 0.0
    record = runtime._load_candidate_record(run_id, candidate_id)
    it = record.iterations[-1]
    assert it.failure_class == "Timeout"
    assert it.score == 0.0


def test_list_iterations_empty_for_fresh_candidate(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    iterations = runtime.list_iterations(run_id, tasks[0].candidate_id)
    assert iterations == []


def test_run_verifier_records_iteration_with_agent_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "SearchCandidateAgent",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    candidate_id = tasks[0].candidate_id
    session = runtime.start_agent_session(run_id, candidate_id, {"goal": "iterate"})

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"combined_score": 0.7, "valid": true}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, candidate_id, agent_session_id=session.agent_session_id)

    record = runtime._load_candidate_record(run_id, candidate_id)
    it = record.iterations[-1]
    assert it.agent_session_id == session.agent_session_id

    refreshed = runtime._load_agent_session_by_id(session.agent_session_id, run_id=run_id)
    assert refreshed.counters.get("verifier_runs") == 1


def test_run_verifier_without_agent_session_id_is_main_final_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "SearchCandidateAgent",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    candidate_id = tasks[0].candidate_id
    runtime.start_agent_session(run_id, candidate_id, {"goal": "iterate"})

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"combined_score": 0.6, "valid": true}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    # Main final verify call - no agent_session_id, no auto-attribution.
    report = runtime.run_verifier(run_id, candidate_id)
    assert report.aggregate_score == 0.6

    record = runtime._load_candidate_record(run_id, candidate_id)
    it = record.iterations[-1]
    assert it.agent_session_id is None


def test_removed_runtime_methods_are_absent() -> None:
    """Defensive guardrail: lifecycle/observation methods must not be
    reintroduced on the runtime."""
    for name in (
        "update_agent_status",
        "list_agent_status",
        "sync_host_agent_sessions",
        "_observe_opencode_session",
        "_finish_agent_session_from_host",
        "_host_observation_reason",
        "finish_agent_session",
        "abort_agent_session",
        "abort_all_agent_sessions",
        "_abort_agent_session_record",
        "publish_observation",
        "list_observations",
        "wait_agent_events",
        "_active_agent_session_count",
        "_append_agent_event",
        "_write_agent_event",
        "_load_agent_events",
        "submit_candidate",
        "next_batch",
    ):
        assert not hasattr(FileSearchRuntime, name), (
            f"FileSearchRuntime.{name} should be removed"
        )


def test_constructor_does_not_accept_opencode_db_path() -> None:
    import inspect

    signature = inspect.signature(FileSearchRuntime.__init__)
    assert "opencode_db_path" not in signature.parameters


def test_run_verifier_parses_subprocess_metrics_with_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    run_id, candidate_id, workspace = create_candidate(runtime, project)
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='debug line\n{"combined_score": 0.75, "valid": true}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)

    report = runtime.run_verifier(run_id, candidate_id)

    assert report.process_passed is True
    assert report.aggregate_score == 0.75
    assert calls[0][1]["cwd"] == workspace.resolve()
    assert "PYTHONPATH" in calls[0][1]["env"]


def test_run_verifier_handles_subprocess_timeout_with_mock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    run_id, candidate_id, _workspace = create_candidate(runtime, project)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)

    report = runtime.run_verifier(run_id, candidate_id)

    assert report.process_passed is False
    assert report.aggregate_score == 0.0
    assert report.verifier_results[0].failure_class == "Timeout"


def test_select_uses_metric_direction_for_minimize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        spec_for(project, max_candidates=2, direction="minimize"),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    for task in tasks:
        runtime.start_agent_session(run_id, task.candidate_id, {"goal": "submit"})

    def fake_run(*args, **kwargs):
        cwd = Path(kwargs["cwd"])
        score = 0.1 if cwd.name == "c002" else 0.9
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f'{{"combined_score": {score}}}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, "c001")
    runtime.run_verifier(run_id, "c002")

    selection = runtime.select(run_id)

    assert selection["selected_candidate_id"] == "c002"
    assert selection["selected_score"] == 0.1


def test_select_uses_best_iteration_when_artifact_is_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        spec_for(project, max_candidates=2),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    for task in tasks:
        (task.workspace / "initial_program.py").write_text(
            f"VALUE = {task.candidate_id!r}\n", encoding="utf-8"
        )

    scores_by_candidate = {
        "c001": [0.9, 0.4, 0.9],
        "c002": [0.7],
    }
    real_run = subprocess.run

    def fake_run(*args, **kwargs):
        command = args[0]
        if command and command[0] != "python":
            return real_run(*args, **kwargs)
        candidate_id = Path(kwargs["cwd"]).name
        score = scores_by_candidate[candidate_id].pop(0)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=f'{{"combined_score": {score}, "valid": true}}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)

    runtime.run_verifier(run_id, "c001")
    runtime.run_verifier(run_id, "c001")
    runtime.run_verifier(run_id, "c002")

    selection = runtime.select(run_id)

    assert selection["selected_candidate_id"] == "c001"
    assert selection["selected_score"] == 0.9
    assert selection["selected_iteration"] == 1


def test_select_can_recover_best_iteration_after_artifact_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        spec_for(project, max_candidates=2),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    scores_by_candidate = {
        "c001": [0.9, 0.4, 0.9],
        "c002": [0.7, 0.7],
    }
    real_run = subprocess.run

    def fake_run(*args, **kwargs):
        command = args[0]
        if command and command[0] != "python":
            return real_run(*args, **kwargs)
        candidate_id = Path(kwargs["cwd"]).name
        score = scores_by_candidate[candidate_id].pop(0)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=f'{{"combined_score": {score}, "valid": true}}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)

    c001_workspace = tasks[0].workspace
    c001_workspace.joinpath("initial_program.py").write_text(
        "VALUE = 'fast'\n", encoding="utf-8"
    )
    runtime.run_verifier(run_id, "c001")
    c001_workspace.joinpath("initial_program.py").write_text(
        "VALUE = 'slow'\n", encoding="utf-8"
    )
    runtime.run_verifier(run_id, "c001")

    tasks[1].workspace.joinpath("initial_program.py").write_text(
        "VALUE = 'middle'\n", encoding="utf-8"
    )
    runtime.run_verifier(run_id, "c002")

    selection = runtime.select(run_id)

    assert selection["selected_candidate_id"] == "c001"
    assert selection["selected_iteration"] == 1
    assert selection["selected_score"] == 0.9
    assert tasks[0].workspace.joinpath("initial_program.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 'fast'\n"


def test_run_verifier_rejects_missing_results_ledger_git_history(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    results_before = tasks[0].workspace.joinpath("results.tsv").read_text(
        encoding="utf-8"
    )
    shutil.rmtree(tasks[0].workspace / ".git")

    with pytest.raises(RuntimeError, match="ResultsLedgerMutation"):
        runtime.run_verifier(run_id, tasks[0].candidate_id)

    assert tasks[0].workspace.joinpath("results.tsv").read_text(
        encoding="utf-8"
    ) == results_before
    assert runtime.list_iterations(run_id, tasks[0].candidate_id) == []


def test_run_verifier_records_real_git_commit_for_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    run_id, candidate_id, workspace = create_candidate(runtime, project)
    workspace.joinpath("initial_program.py").write_text(
        "VALUE = 'committed'\n", encoding="utf-8"
    )

    real_run = subprocess.run

    def fake_run(*args, **kwargs):
        command = args[0]
        if command and command[0] != "python":
            return real_run(*args, **kwargs)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='{"combined_score": 0.9, "valid": true}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)

    runtime.run_verifier(run_id, candidate_id)

    iteration = runtime.list_iterations(run_id, candidate_id)[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True
    ).strip()
    assert iteration["ledger_git_head"] == head
    assert iteration["git_head"] != head
    assert iteration["git_artifact_clean"] is True


def test_results_tsv_is_committed_and_runtime_enforces_one_row_per_report(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    run_id, candidate_id, workspace = create_candidate(runtime, project)
    results_path = workspace / "results.tsv"

    assert results_path.read_text(encoding="utf-8") == (
        "commit\tcombined_score\tstatus\thypothesis\n"
    )
    assert not (workspace / ".tmp" / "results.tsv").exists()
    assert subprocess.check_output(
        ["git", "ls-files", "--", "results.tsv"],
        cwd=workspace,
        text=True,
    ).strip() == "results.tsv"
    assert subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--", "results.tsv"],
        cwd=workspace,
        text=True,
    ).strip() == ""

    session = runtime.start_agent_session(run_id, candidate_id)
    first = runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session.agent_session_id,
        hypothesis="measure inherited baseline",
    )
    assert first.process_passed is True
    first_text = results_path.read_text(encoding="utf-8")
    assert len(first_text.splitlines()) == 2
    assert first_text.splitlines()[1].endswith(
        "\tpass\tmeasure inherited baseline"
    )

    (workspace / "config.yaml").write_text("name: denied edit\n", encoding="utf-8")
    second = runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session.agent_session_id,
        hypothesis="probe denied configuration change",
    )
    assert second.process_passed is False
    second_text = results_path.read_text(encoding="utf-8")
    assert second_text.startswith(first_text)
    assert len(second_text.splitlines()) == 3
    assert second_text.splitlines()[2].endswith(
        "\tfail\tprobe denied configuration change"
    )

    record = runtime._load_candidate_record(run_id, candidate_id)
    assert len(record.iterations) == 2
    assert len(record.results_ledger) == 2
    assert record.iterations[-1].ledger_git_head == record.results_ledger_git_head
    assert subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True
    ).strip() == record.results_ledger_git_head

    redispatched = runtime.redispatch_candidate(run_id, candidate_id)
    context = runtime.get_agent_context(redispatched.agent_session_id)
    assert context["results_tsv"] == str(results_path)
    assert len(context["results"]) == 2

    results_path.write_text(
        second_text.replace("measure inherited baseline", "rewritten baseline"),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="ResultsLedgerMutation"):
        runtime.run_verifier(run_id, candidate_id, hypothesis="must not run")
    assert len(runtime._load_candidate_record(run_id, candidate_id).results_ledger) == 2


def test_results_tsv_inherits_across_derived_candidate_and_successor_run(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(project, {"name": "evolve"}, max_candidates=3)
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    first_run_id = runtime.create_run(frozen.frozen_spec_id)

    first_plan = runtime.plan_next(first_run_id, requested_k=1)
    parent = runtime.start_batch(first_run_id, first_plan.plan_id)[0]
    runtime.run_verifier(
        first_run_id,
        parent.candidate_id,
        hypothesis="parent design",
    )
    parent_results = parent.workspace.joinpath("results.tsv").read_text(
        encoding="utf-8"
    )

    second_plan = runtime.plan_next(first_run_id, requested_k=1)
    child = runtime.start_batch(first_run_id, second_plan.plan_id)[0]
    assert child.base_candidate_id == parent.candidate_id
    assert child.workspace.joinpath("results.tsv").read_text(
        encoding="utf-8"
    ) == parent_results
    runtime.run_verifier(
        first_run_id,
        child.candidate_id,
        hypothesis="child design",
    )
    child_results = child.workspace.joinpath("results.tsv").read_text(
        encoding="utf-8"
    )
    assert child_results.startswith(parent_results)
    assert len(child_results.splitlines()) == len(parent_results.splitlines()) + 1

    successor_run_id = runtime.create_run(
        frozen.frozen_spec_id,
        source_run_id=first_run_id,
    )
    successor_plan = runtime.plan_next(successor_run_id, requested_k=1)
    successor = runtime.start_batch(successor_run_id, successor_plan.plan_id)[0]
    assert successor.workspace.joinpath("results.tsv").read_text(
        encoding="utf-8"
    ) == parent_results
    successor_record = runtime._load_candidate_record(
        successor_run_id,
        successor.candidate_id,
    )
    assert [entry.source_run_id for entry in successor_record.results_ledger] == [
        first_run_id
    ]
    assert subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--", "results.tsv"],
        cwd=successor.workspace,
        text=True,
    ).strip() == ""


def test_legacy_tmp_results_tsv_migrates_and_backfills_missing_iterations(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    run_id, candidate_id, workspace = create_candidate(runtime, project)
    runtime.run_verifier(run_id, candidate_id, hypothesis="legacy row kept")
    runtime.run_verifier(run_id, candidate_id, hypothesis="missing legacy row")

    current_lines = workspace.joinpath("results.tsv").read_text(
        encoding="utf-8"
    ).splitlines()
    legacy_path = workspace / ".tmp" / "results.tsv"
    legacy_path.write_text("\n".join(current_lines[:2]) + "\n", encoding="utf-8")
    workspace.joinpath("results.tsv").unlink()

    candidate_path = runtime._candidate_dir(run_id, candidate_id) / "candidate.json"
    legacy_record = load_json(candidate_path)
    legacy_record.pop("results_ledger", None)
    legacy_record.pop("results_ledger_git_head", None)
    for iteration in legacy_record["iterations"]:
        iteration.pop("ledger_git_head", None)
        iteration.pop("hypothesis", None)
        iteration["summary"] = ""
    write_json(candidate_path, legacy_record)

    session = runtime.start_agent_session(run_id, candidate_id)
    context = runtime.get_agent_context(session.agent_session_id)
    migrated_lines = workspace.joinpath("results.tsv").read_text(
        encoding="utf-8"
    ).splitlines()

    assert len(migrated_lines) == 3
    assert migrated_lines[1] == current_lines[1]
    assert migrated_lines[2].endswith("\tpass\trecovered iteration 2")
    assert len(context["results"]) == 2
    migrated_record = runtime._load_candidate_record(run_id, candidate_id)
    assert migrated_record.results_ledger_git_head is not None
    assert len(migrated_record.results_ledger) == 2


def test_select_checks_out_best_git_commit_before_final_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        spec_for(project, max_candidates=2),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    c001_workspace = tasks[0].workspace
    c002_workspace = tasks[1].workspace

    real_run = subprocess.run

    def fake_run(*args, **kwargs):
        command = args[0]
        if command and command[0] == "python":
            content = Path(kwargs["cwd"], "initial_program.py").read_text(
                encoding="utf-8"
            )
            score = 0.9 if "fast" in content else 0.4 if "slow" in content else 0.7
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=f'{{"combined_score": {score}, "valid": true}}\n',
                stderr="",
            )
        return real_run(*args, **kwargs)

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)

    c001_workspace.joinpath("initial_program.py").write_text(
        "VALUE = 'fast'\n", encoding="utf-8"
    )
    fast_commit = git_commit_all(c001_workspace, "fast version")
    runtime.run_verifier(run_id, "c001")

    c001_workspace.joinpath("initial_program.py").write_text(
        "VALUE = 'slow'\n", encoding="utf-8"
    )
    git_commit_all(c001_workspace, "slow version")
    runtime.run_verifier(run_id, "c001")

    c002_workspace.joinpath("initial_program.py").write_text(
        "VALUE = 'middle'\n", encoding="utf-8"
    )
    git_commit_all(c002_workspace, "middle version")
    runtime.run_verifier(run_id, "c002")

    selection = runtime.select(run_id)

    assert selection["selected_candidate_id"] == "c001"
    assert selection["selected_iteration"] == 1
    assert selection["selected_git_head"] == fast_commit
    assert selection["selected_score"] == 0.9
    assert c001_workspace.joinpath("initial_program.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 'fast'\n"
    final_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=c001_workspace, text=True
    ).strip()
    selected_record = runtime._load_candidate_record(run_id, "c001")
    assert final_head == selected_record.results_ledger_git_head
    assert final_head != fast_commit


def test_run_verifier_rejects_mismatched_agent_session(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
        },
        max_candidates=2,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    session_for_c0 = runtime.start_agent_session(
        run_id, tasks[0].candidate_id, {"goal": "c0"}
    )
    other_session = runtime.start_agent_session(
        run_id, tasks[1].candidate_id, {"goal": "c1"}
    )

    with pytest.raises(ValueError, match="agent_session_id does not belong"):
        runtime.run_verifier(
            run_id,
            tasks[0].candidate_id,
            agent_session_id=other_session.agent_session_id,
        )


def test_concurrent_run_verifiers_preserve_best_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
        },
        max_candidates=2,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    both_verifiers_started = threading.Barrier(2)
    high_score_committed = threading.Event()
    errors: list[BaseException] = []

    def fake_run(*args, **kwargs):
        cwd = Path(kwargs["cwd"])
        both_verifiers_started.wait(timeout=5)
        if cwd.name == "c002":
            assert high_score_committed.wait(timeout=5)
            score = 0.1
        else:
            score = 0.9
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f'{{"combined_score": {score}}}\n',
            stderr="",
        )

    def verify(candidate_id: str) -> None:
        try:
            runtime.run_verifier(run_id, candidate_id)
            if candidate_id == "c001":
                high_score_committed.set()
        except BaseException as exc:  # pragma: no cover - surfaced after join
            errors.append(exc)

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)

    high = threading.Thread(target=verify, args=(tasks[0].candidate_id,))
    low = threading.Thread(target=verify, args=(tasks[1].candidate_id,))
    high.start()
    low.start()
    high.join(timeout=10)
    low.join(timeout=10)

    assert not high.is_alive()
    assert not low.is_alive()
    assert errors == []

    run = runtime._load_run(run_id)
    assert run.best_candidate_id == "c001"
    assert run.best_score == 0.9
    assert run.candidates_evaluated == 2


def test_run_verifier_works_without_session_and_records_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "SearchCandidateAgent",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    candidate_id = tasks[0].candidate_id
    session = runtime.start_agent_session(run_id, candidate_id, {"goal": "iterate"})

    scores = [0.4, 0.7, 0.9]

    def fake_run(*args, **kwargs):
        score = scores.pop(0)
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f'{{"combined_score": {score}, "valid": true}}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)

    for expected_score in [0.4, 0.7, 0.9]:
        report = runtime.run_verifier(
            run_id, candidate_id, agent_session_id=session.agent_session_id
        )
        assert report.aggregate_score == expected_score

    record = runtime._load_candidate_record(run_id, candidate_id)
    assert len(record.iterations) == 3
    assert [it.score for it in record.iterations] == [0.4, 0.7, 0.9]
    assert [it.iteration for it in record.iterations] == [1, 2, 3]
    assert record.score_report.aggregate_score == 0.9  # type: ignore[union-attr]


def test_list_iterations_returns_all_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    candidate_id = tasks[0].candidate_id
    session = runtime.start_agent_session(run_id, candidate_id, {"goal": "iterate"})

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"combined_score": 0.5, "valid": true}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, candidate_id, agent_session_id=session.agent_session_id)
    runtime.run_verifier(run_id, candidate_id, agent_session_id=session.agent_session_id)

    iterations = runtime.list_iterations(run_id, candidate_id)
    assert len(iterations) == 2
    assert iterations[0]["iteration"] == 1
    assert iterations[1]["iteration"] == 2
    assert all(it["agent_session_id"] == session.agent_session_id for it in iterations)


def test_get_agent_context_returns_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    candidate_id = tasks[0].candidate_id
    session = runtime.start_agent_session(run_id, candidate_id, {"goal": "iterate"})

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"combined_score": 0.42, "valid": true}\n',
            stderr="",
        )

    monkeypatch.setattr("goal_plus.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, candidate_id, agent_session_id=session.agent_session_id)

    context = runtime.get_agent_context(session.agent_session_id)
    assert "iterations" in context
    assert len(context["iterations"]) == 1
    assert context["iterations"][0]["iteration"] == 1
    assert context["iterations"][0]["score"] == 0.42
    assert context["iterations"][0]["agent_session_id"] == session.agent_session_id


def test_history_and_report_include_agent_sessions(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "SearchCandidateAgent",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id, {"goal": "document session"})

    history = runtime.list_history(run_id)
    candidate = history["candidates"][0]
    assert candidate["agent_sessions"][0]["agent_session_id"] == session.agent_session_id

    report_path = runtime.report(run_id)
    report = report_path.read_text(encoding="utf-8")
    assert "## Agent Sessions" in report
    assert session.agent_session_id in report


@pytest.mark.pi
def test_history_projects_latest_structured_research_handoff(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {"max_runtime_seconds": 600},
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)
    runtime.bind_agent_handle(
        session.agent_session_id,
        {
            "host": "pi-rpc",
            "external_id": "pi-session-1",
            "metadata": {
                "progress_handoff": {
                    "summary": "reworked scratch residency",
                    "model_handoff": {
                        "summary": "reworked scratch residency",
                        "key_results": [
                            {
                                "artifact": "iteration 3",
                                "code_surface": "kernel.py:build_schedule",
                                "change": "keep scratch values resident",
                                "portability": "standalone",
                                "depends_on": [],
                                "measured_effect": "score 5.0 -> 7.0",
                                "verifier_result": "score 7.0",
                                "relation_to_incumbent": "orthogonal",
                                "conclusion": "batch reuse is promising",
                            }
                        ],
                        "pitfalls": [
                            {
                                "condition": "when the gather spans six lanes",
                                "failed_approach": "fully interleave all loads",
                                "reason": "scratch pressure causes spills",
                                "recommendation": "keep two lanes staged",
                            }
                        ],
                        "blockers": ["no cheap slot-occupancy probe"],
                        "next_steps": ["test four-way interleave"],
                        "verifier_assessment": {
                            "status": "adequate",
                            "evidence": ["local ranking is deterministic"],
                            "impact": "safe to compare variants",
                            "recommended_action": "keep_spec",
                        },
                    },
                }
            },
        },
    )

    history = runtime.list_history(run_id)
    candidate = history["candidates"][0]

    assert candidate["summary"] == "reworked scratch residency"
    assert candidate["key_results"][0]["artifact"] == "iteration 3"
    assert candidate["feature_ledger"][0]["code_surface"] == (
        "kernel.py:build_schedule"
    )
    assert candidate["verifier_assessment"]["status"] == "adequate"
    assert history["feature_ledger"][0]["relation_to_incumbent"] == "orthogonal"
    assert history["verifier_assessments"][0]["candidate_id"] == task.candidate_id
    assert history["research_rollup"]["pitfalls"][0]["scope"] == "candidate_local"
    assert history["pitfalls"] == history["research_rollup"]["pitfalls"]
    assert history["research_rollup"]["pitfalls"][0]["confidence"] == (
        "single_observation"
    )
    assert candidate["risk_notes"][0].startswith(
        "Condition: when the gather spans six lanes; "
        "failed approach: fully interleave all loads"
    )
    assert candidate["research_summary"]["pitfalls"][0]["condition"] == (
        "when the gather spans six lanes"
    )
    assert candidate["blockers"] == ["no cheap slot-occupancy probe"]
    assert candidate["next_ideas"] == ["test four-way interleave"]
    assert candidate["research_summary"]["source_agent_session_id"] == (
        session.agent_session_id
    )


def test_history_feature_ledger_retains_non_frontier_candidate(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {"max_runtime_seconds": 600},
        },
        max_candidates=2,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    for task in tasks:
        session = runtime.start_agent_session(run_id, task.candidate_id)
        runtime.bind_agent_handle(
            session.agent_session_id,
            {
                "host": "pi-rpc",
                "external_id": f"pi-{task.candidate_id}",
                "metadata": {
                    "progress_handoff": {
                        "model_handoff": {
                            "summary": f"result from {task.candidate_id}",
                            "key_results": [
                                {
                                    "artifact": "iteration 1",
                                    "code_surface": f"feature-{task.candidate_id}",
                                    "change": "candidate-specific feature",
                                    "portability": "standalone",
                                    "depends_on": [],
                                    "measured_effect": "0.0 -> 1.0",
                                    "verifier_result": "passed",
                                    "relation_to_incumbent": "orthogonal",
                                    "conclusion": "portable",
                                }
                            ],
                            "pitfalls": [],
                            "blockers": [],
                            "next_steps": [],
                            "verifier_assessment": {
                                "status": "unknown",
                                "evidence": [],
                                "impact": "",
                                "recommended_action": "keep_spec",
                            },
                        }
                    }
                },
            },
        )

    history = runtime.list_history(run_id, top_n=1)

    assert history["returned_candidates"] == 1
    assert {entry["candidate_id"] for entry in history["feature_ledger"]} == {
        "c001",
        "c002",
    }
    hidden = next(
        entry for entry in history["feature_ledger"] if entry["candidate_id"] == "c002"
    )
    assert hidden["candidate_visible"] is False


def test_invalidate_run_fences_work_and_successor_inherits_research(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {"name": "agent_guided", "worker_mode": "agent-session-pool"},
        max_candidates=2,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(
        run_id,
        plan.plan_id,
        [CandidateProposal(intent="bootstrap")],
    )[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)
    runtime.bind_agent_handle(
        session.agent_session_id,
        {
            "host": "opencode",
            "metadata": {
                "progress_handoff": {
                    "model_handoff": {
                        "summary": "found a portable fusion",
                        "key_results": [
                            {
                                "artifact": "iteration 1",
                                "code_surface": "kernel.py:hash_stage",
                                "change": "fuse stages 0/2/4",
                                "portability": "standalone",
                                "depends_on": [],
                                "measured_effect": "1.0 -> 2.0",
                                "verifier_result": "passed",
                                "relation_to_incumbent": "orthogonal",
                                "conclusion": "probe against the next incumbent",
                            }
                        ],
                        "pitfalls": [
                            {
                                "scope": "feature_family",
                                "condition": "when all lanes share one scratch bank",
                                "failed_approach": "fully interleave writes",
                                "observed_result": "score regressed",
                                "reason": "bank pressure",
                                "evidence_artifact": "iteration 1",
                                "confidence": "single_observation",
                                "recommendation": "apply only with separate banks",
                            }
                        ],
                        "blockers": [],
                        "next_steps": ["transfer fusion"],
                        "verifier_assessment": {
                            "status": "concern",
                            "evidence": ["required edge case is absent"],
                            "impact": "ranking can accept invalid artifacts",
                            "recommended_action": "upgrade_spec",
                        },
                    }
                }
            },
        },
    )
    runtime.run_verifier(
        run_id,
        task.candidate_id,
        agent_session_id=session.agent_session_id,
    )

    invalidated = runtime.invalidate_run(
        run_id,
        reason="verifier_coverage_inadequate",
        summary="main agent confirmed the missing required edge case",
        evidence=[{"case": "required-edge", "source_candidate_id": "c001"}],
    )

    assert invalidated.state == RunState.ABORTED
    assert invalidated.invalidation_reason == "verifier_coverage_inadequate"
    with pytest.raises(RuntimeError, match="invalidated"):
        runtime.run_verifier(run_id, task.candidate_id)
    with pytest.raises(RuntimeError):
        runtime.plan_next(run_id, requested_k=1)
    with pytest.raises(RuntimeError):
        runtime.start_agent_session(run_id, task.candidate_id)
    with pytest.raises(RuntimeError, match="invalidated"):
        runtime.select(run_id)

    successor_id = runtime.create_run(
        frozen.frozen_spec_id,
        source_run_id=run_id,
    )
    successor_history = runtime.list_history(successor_id)
    inherited = successor_history["inherited_research"]

    assert successor_history["source_run_id"] == run_id
    assert inherited["frontier"][0]["candidate_id"] == "c001"
    assert inherited["feature_ledger"][0]["code_surface"] == (
        "kernel.py:hash_stage"
    )
    assert inherited["feature_ledger"][0]["score_reusable"] is False
    assert inherited["pitfalls"][0]["scope"] == "feature_family"
    assert runtime.status(run_id).replacement_run_id == successor_id


def test_invalidation_rejects_in_flight_verifier_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    original_run_commands = runtime._run_commands

    def invalidate_after_execution(*args: object, **kwargs: object) -> ScoreReport:
        report = original_run_commands(*args, **kwargs)  # type: ignore[arg-type]
        runtime.invalidate_run(
            run_id,
            reason="verifier_target_mismatch",
            summary="main agent confirmed target mismatch while verifier ran",
            evidence=[{"target": "hidden judge"}],
        )
        return report

    monkeypatch.setattr(runtime, "_run_commands", invalidate_after_execution)

    with pytest.raises(RuntimeError, match="record verifier result"):
        runtime.run_verifier(run_id, task.candidate_id)

    assert runtime.status(run_id).state == RunState.ABORTED
    assert runtime.list_iterations(run_id, task.candidate_id) == []


def test_runtime_does_not_create_event_or_observation_dirs(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    runtime.start_agent_session(run_id, tasks[0].candidate_id, {"goal": "iterate"})

    run_dir = runtime._run_dir(run_id)
    assert not (run_dir / "agent_events").exists()
    assert not (run_dir / "observations").exists()
