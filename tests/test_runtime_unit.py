from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic_any_search_mcp.models import ArtifactBundle, CandidateProposal, SearchSpec
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


def spec_with_strategy(
    project: Path,
    strategy: dict,
    *,
    max_candidates: int = 4,
) -> SearchSpec:
    data = spec_for(project, max_candidates=max_candidates).model_dump(mode="json")
    data["strategy"] = strategy
    return SearchSpec.model_validate(data)


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
    (source / ".tmp").mkdir()
    (source / ".tmp" / "scratch.py").write_text("print('scratch')\n", encoding="utf-8")
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


def test_plan_next_and_start_batch_record_plan_metadata(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=2), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)

    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    assert plan.strategy.name == "independent_branches"
    assert plan.worker_policy["mode"] == "main-agent-search-direct"
    assert plan.worker_policy["requires_dispatch"] is False
    assert plan.planned_k == 2
    assert [task.candidate_id for task in tasks] == ["c001", "c002"]
    assert tasks[0].plan_id == "plan_001"
    assert tasks[0].proposal.intent == "Independent candidate c001"
    assert (tasks[0].workspace / ".tmp").is_dir()
    assert any(".tmp" in instruction for instruction in tasks[0].instructions)

    saved_plan = runtime._load_plan(run_id, "plan_001")
    assert saved_plan.status == "started"
    assert saved_plan.started_candidate_ids == ["c001", "c002"]


def test_dispatch_worker_mode_is_planned_and_required_for_submission(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "sub-agent-search-dispatch",
            "worker_agent_type": "AnySearchAgent",
            "worker_timeout_seconds": 120,
            "worker_local_verifier_max_runs": 3,
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    assert plan.worker_policy["mode"] == "sub-agent-search-dispatch"
    assert plan.worker_policy["subagent_type"] == "AnySearchAgent"
    assert plan.worker_policy["timeout_seconds"] == 120
    assert plan.worker_policy["local_verifier_max_runs"] == 3
    assert plan.worker_policy["requires_dispatch"] is True
    assert tasks[0].strategy_metadata["worker_mode"] == "sub-agent-search-dispatch"
    assert any(
        "worker_mode=sub-agent-search-dispatch" in instruction
        for instruction in tasks[0].instructions
    )
    assert any("subagent_type='AnySearchAgent'" in instruction for instruction in tasks[0].instructions)
    assert any("Worker timeout is 120 seconds" in instruction for instruction in tasks[0].instructions)
    assert any("at most 3 times" in instruction for instruction in tasks[0].instructions)
    assert any("bounded and fast" in instruction for instruction in tasks[0].instructions)
    assert any("score targets" in instruction for instruction in tasks[0].instructions)

    with pytest.raises(ValueError, match="dispatch_id"):
        runtime.submit_candidate(
            run_id,
            tasks[0].candidate_id,
            ArtifactBundle(candidate_id=tasks[0].candidate_id, status="patch_ready"),
        )

    dispatch = runtime.prepare_worker(run_id, tasks[0].candidate_id, {"goal": "try worker"})
    context = runtime.get_worker_context(dispatch.dispatch_id)
    assert context["timeout_seconds"] == 120
    assert context["local_verifier_max_runs"] == 3
    assert context["local_validation_policy"]["max_verifier_runs"] == 3
    assert context["deadline_at"].endswith("Z")
    assert "120s" in dispatch.worker_brief
    assert "Local verifier limit: `3` runs" in dispatch.worker_brief
    assert "best-so-far" in dispatch.worker_brief
    runtime.submit_candidate(
        run_id,
        tasks[0].candidate_id,
        ArtifactBundle(
            candidate_id=tasks[0].candidate_id,
            status="patch_ready",
            dispatch_id=dispatch.dispatch_id,
            context_hash=dispatch.context_hash,
        ),
    )
    record = runtime._load_candidate_record(run_id, tasks[0].candidate_id)
    assert record.status == "submitted"


def test_prepare_worker_persists_dispatch_and_context(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=2), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    task = runtime.next_batch(run_id, 1)[0]

    dispatch = runtime.prepare_worker(
        run_id,
        task.candidate_id,
        {
            "goal": "try a weighted local search",
            "expected_output": "edit initial_program.py and summarize the result",
        },
    )
    context = runtime.get_worker_context(dispatch.dispatch_id)

    assert dispatch.dispatch_id.startswith(f"dispatch_{run_id.removeprefix('run_')}_")
    assert dispatch.candidate_id == task.candidate_id
    assert dispatch.brief_path.exists()
    assert dispatch.dispatch_path.exists()
    assert "search_get_worker_context" in dispatch.worker_brief
    assert context["dispatch_id"] == dispatch.dispatch_id
    assert context["context_hash"] == dispatch.context_hash
    assert context["main_directive"]["goal"] == "try a weighted local search"
    assert context["workspace"] == str(task.workspace)
    assert context["scratch_dir"] == str(task.workspace / ".tmp")
    assert context["allowed_files"] == ["initial_program.py"]
    assert context["denied_files"] == ["evaluator.py", "config.yaml"]
    assert context["process_verifiers"][0]["name"] == "score"
    assert context["protocol"]["authority"].startswith("MCP context is authoritative")
    assert context["timeout_seconds"] == 600
    assert context["local_verifier_max_runs"] == 0
    assert context["local_validation_policy"]["max_verifier_runs"] == 0
    assert context["local_validation_policy"]["actual_verifier_allowed"] is False
    assert "Scratch files are for notes" in context["local_validation_policy"]["scratch_rule"]
    assert "rm" in context["local_validation_policy"]["forbidden_destructive_commands"]
    assert "git clean" in context["local_validation_policy"]["forbidden_destructive_commands"]
    assert "score targets" in context["protocol"]["directive_rule"]
    assert context["deadline_at"].endswith("Z")
    assert "deadline_at" in context["protocol"]["timeout_rule"]
    assert "Do not run the process verifier" in context["protocol"]["local_validation_rule"]
    assert "Do not delete" in context["protocol"]["destructive_command_rule"]
    assert "Timeout: `600s`" in dispatch.worker_brief
    assert "Local verifier limit: `0` runs" in dispatch.worker_brief
    assert "Do not run the process verifier" in dispatch.worker_brief
    assert "Do not create or run scratch experiment scripts" in dispatch.worker_brief
    assert "Do not delete" in dispatch.worker_brief
    assert "score targets" in dispatch.worker_brief


def test_prepare_worker_allows_per_dispatch_timeout_override(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    task = runtime.next_batch(run_id, 1)[0]

    dispatch = runtime.prepare_worker(
        run_id,
        task.candidate_id,
        "short exploratory attempt",
        timeout_seconds=30,
    )
    context = runtime.get_worker_context(dispatch.dispatch_id)

    assert context["timeout_seconds"] == 30
    assert "Timeout: `30s`" in dispatch.worker_brief

    with pytest.raises(ValueError, match="timeout_seconds"):
        runtime.prepare_worker(run_id, task.candidate_id, timeout_seconds=0)


def test_submit_candidate_validates_worker_dispatch_context_hash(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=2), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    tasks = runtime.next_batch(run_id, 2)
    dispatch = runtime.prepare_worker(run_id, tasks[0].candidate_id, "try one concrete variant")

    with pytest.raises(ValueError, match="context_hash is required"):
        runtime.submit_candidate(
            run_id,
            tasks[0].candidate_id,
            ArtifactBundle(
                candidate_id=tasks[0].candidate_id,
                status="patch_ready",
                dispatch_id=dispatch.dispatch_id,
            ),
        )

    with pytest.raises(ValueError, match="context_hash"):
        runtime.submit_candidate(
            run_id,
            tasks[0].candidate_id,
            ArtifactBundle(
                candidate_id=tasks[0].candidate_id,
                status="patch_ready",
                dispatch_id=dispatch.dispatch_id,
                context_hash="wrong",
            ),
        )

    with pytest.raises(ValueError, match="does not belong"):
        runtime.submit_candidate(
            run_id,
            tasks[1].candidate_id,
            ArtifactBundle(
                candidate_id=tasks[1].candidate_id,
                status="patch_ready",
                dispatch_id=dispatch.dispatch_id,
                context_hash=dispatch.context_hash,
            ),
        )

    runtime.submit_candidate(
        run_id,
        tasks[0].candidate_id,
        ArtifactBundle(
            candidate_id=tasks[0].candidate_id,
            status="patch_ready",
            dispatch_id=dispatch.dispatch_id,
            context_hash=dispatch.context_hash,
            summary="implemented the dispatched idea",
            next_ideas=["try a smaller mutation next"],
        ),
    )
    record = runtime._load_candidate_record(run_id, tasks[0].candidate_id)
    assert record.artifact.dispatch_id == dispatch.dispatch_id
    assert record.artifact.context_hash == dispatch.context_hash


def test_history_and_report_include_worker_dispatches(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    task = runtime.next_batch(run_id, 1)[0]
    dispatch = runtime.prepare_worker(run_id, task.candidate_id, {"goal": "document dispatch"})
    runtime.submit_candidate(
        run_id,
        task.candidate_id,
        ArtifactBundle(
            candidate_id=task.candidate_id,
            status="patch_ready",
            dispatch_id=dispatch.dispatch_id,
            context_hash=dispatch.context_hash,
            summary="dispatch-aware candidate",
            next_ideas=["inspect report linkage"],
        ),
    )

    history = runtime.list_history(run_id)
    candidate = history["candidates"][0]
    assert candidate["dispatches"][0]["dispatch_id"] == dispatch.dispatch_id
    assert candidate["artifact_dispatch_id"] == dispatch.dispatch_id
    assert candidate["next_ideas"] == ["inspect report linkage"]

    report_path = runtime.report(run_id)
    report = report_path.read_text(encoding="utf-8")
    assert "## Worker Dispatches" in report
    assert dispatch.dispatch_id in report
    assert "goal=document dispatch" in report


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
    assert plan2.proposal_contract.must_reference_one_of == ["c001"]
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
    tasks = runtime.next_batch(run_id, 2)
    (tasks[0].workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tasks[1].workspace / "initial_program.py").write_text("VALUE = 2\n", encoding="utf-8")

    for task in tasks:
        runtime.submit_candidate(
            run_id,
            task.candidate_id,
            ArtifactBundle(candidate_id=task.candidate_id, status="patch_ready"),
        )

    def fake_run(*args, **kwargs):
        cwd = Path(kwargs["cwd"])
        score = 0.9 if cwd.name == "c002" else 0.1
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f'{{"combined_score": {score}}}\n',
            stderr="",
        )

    monkeypatch.setattr("agentic_any_search_mcp.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, "c001")
    runtime.run_verifier(run_id, "c002")

    plan = runtime.plan_next(run_id, 2)
    followups = runtime.start_batch(run_id, plan.plan_id)

    assert plan.strategy_trace["parent_candidate_id"] == "c002"
    assert followups[0].base_candidate_id == "c002"
    assert followups[0].parent_candidate_ids == ["c002"]
    assert (followups[0].workspace / "initial_program.py").read_text(encoding="utf-8") == "VALUE = 2\n"


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
    assert plan.proposal_contract.count == 2


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


def test_submit_candidate_ignores_workspace_tmp_scratch(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    task = runtime.next_batch(run_id, 1)[0]
    (task.workspace / ".tmp" / "prototype.py").write_text("print('scratch')\n", encoding="utf-8")

    runtime.submit_candidate(
        run_id,
        task.candidate_id,
        ArtifactBundle(candidate_id=task.candidate_id, status="patch_ready"),
    )
    record = runtime._load_candidate_record(run_id, task.candidate_id)

    assert record.detected_changed_files == []
    assert record.touched_denied_files is False
    assert record.changed_outside_allowed is False


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
