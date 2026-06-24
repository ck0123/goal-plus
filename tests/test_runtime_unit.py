from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic_any_search_mcp.models import ArtifactBundle, SearchSpec
from agentic_any_search_mcp.runtime import (
    FileSearchRuntime,
    canonical_json,
    copy_source_tree,
    list_files,
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
                "wall_clock_seconds": 300,
            },
            "process_verifiers": [
                {
                    "name": "score",
                    "role": "ranking_signal",
                    "command": ["python", "evaluator.py"],
                    "timeout_seconds": 30,
                }
            ],
        }
    )


def create_submitted_candidate(
    runtime: FileSearchRuntime,
    project: Path,
    *,
    direction: str = "maximize",
) -> tuple[str, str, Path]:
    frozen = runtime.freeze_spec(spec_for(project, direction=direction), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    task = runtime.next_batch(run_id, 1)[0]
    runtime.submit_candidate(
        run_id,
        task.candidate_id,
        ArtifactBundle(candidate_id=task.candidate_id, status="patch_ready"),
    )
    return run_id, task.candidate_id, task.workspace


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
    (source / ".search").mkdir()
    (source / ".search" / "run.json").write_text("{}", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "keep.pyc").write_text("compiled", encoding="utf-8")

    destination = tmp_path / "dest"
    copy_source_tree(source, destination)

    listed = [path.relative_to(destination).as_posix() for path in list_files(destination)]
    assert listed == ["keep.py"]


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


def test_next_batch_honors_budget_and_rejects_invalid_k(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=2), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    with pytest.raises(ValueError):
        runtime.next_batch(run_id, 0)

    tasks = runtime.next_batch(run_id, 10)
    assert [task.candidate_id for task in tasks] == ["c001", "c002"]
    assert runtime.next_batch(run_id, 1) == []


def test_submit_candidate_detects_out_of_surface_changes(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    task = runtime.next_batch(run_id, 1)[0]
    (task.workspace / "config.yaml").write_text("name: changed\n", encoding="utf-8")

    runtime.submit_candidate(
        run_id,
        task.candidate_id,
        ArtifactBundle(candidate_id=task.candidate_id, status="patch_ready"),
    )
    record = runtime._load_candidate_record(run_id, task.candidate_id)

    assert record.detected_changed_files == ["config.yaml"]
    assert record.touched_denied_files is True
    assert record.changed_outside_allowed is True


def test_run_verifier_parses_subprocess_metrics_with_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    run_id, candidate_id, workspace = create_submitted_candidate(runtime, project)
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='debug line\n{"combined_score": 0.75, "valid": true}\n',
            stderr="",
        )

    monkeypatch.setattr("agentic_any_search_mcp.runtime.subprocess.run", fake_run)

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
    run_id, candidate_id, _workspace = create_submitted_candidate(runtime, project)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("agentic_any_search_mcp.runtime.subprocess.run", fake_run)

    report = runtime.run_verifier(run_id, candidate_id)

    assert report.process_passed is False
    assert report.aggregate_score == 0.0
    assert report.verifier_results[0].failure_class == "Timeout"


def test_select_uses_metric_direction_for_minimize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=2, direction="minimize"), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    tasks = runtime.next_batch(run_id, 2)
    for task in tasks:
        runtime.submit_candidate(
            run_id,
            task.candidate_id,
            ArtifactBundle(candidate_id=task.candidate_id, status="patch_ready"),
        )

    def fake_run(*args, **kwargs):
        cwd = Path(kwargs["cwd"])
        score = 0.1 if cwd.name == "c002" else 0.9
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f'{{"combined_score": {score}}}\n',
            stderr="",
        )

    monkeypatch.setattr("agentic_any_search_mcp.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, "c001")
    runtime.run_verifier(run_id, "c002")

    selection = runtime.select(run_id)

    assert selection["selected_candidate_id"] == "c002"
    assert selection["selected_score"] == 0.1

