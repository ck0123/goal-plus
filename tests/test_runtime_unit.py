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
    assert plan.worker_policy["requires_agent_session"] is False
    assert plan.planned_k == 2
    assert [task.candidate_id for task in tasks] == ["c001", "c002"]
    assert tasks[0].plan_id == "plan_001"
    assert tasks[0].proposal.intent == "Independent candidate c001"
    assert (tasks[0].workspace / ".tmp").is_dir()
    assert any(".tmp" in instruction for instruction in tasks[0].instructions)

    saved_plan = runtime._load_plan(run_id, "plan_001")
    assert saved_plan.status == "started"
    assert saved_plan.started_candidate_ids == ["c001", "c002"]


def test_agent_session_pool_mode_is_planned_and_required_for_submission(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
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

    assert plan.worker_policy["mode"] == "agent-session-pool"
    assert plan.worker_policy["subagent_type"] == "AnySearchAgent"
    assert plan.worker_policy["timeout_seconds"] == 120
    assert plan.worker_policy["local_verifier_max_runs"] == 3
    assert plan.worker_policy["requires_agent_session"] is True
    assert tasks[0].strategy_metadata["worker_mode"] == "agent-session-pool"
    assert any(
        "worker_mode=agent-session-pool" in instruction
        for instruction in tasks[0].instructions
    )
    assert any("agent_session_id" in instruction for instruction in tasks[0].instructions)
    assert any("subagent_type='AnySearchAgent'" in instruction for instruction in tasks[0].instructions)
    assert any("120 seconds" in instruction for instruction in tasks[0].instructions)
    assert any("at most 3 times" in instruction for instruction in tasks[0].instructions)
    assert any("bounded and fast" in instruction for instruction in tasks[0].instructions)
    assert any("score targets" in instruction for instruction in tasks[0].instructions)

    with pytest.raises(ValueError, match="agent_session_id"):
        runtime.submit_candidate(
            run_id,
            tasks[0].candidate_id,
            ArtifactBundle(candidate_id=tasks[0].candidate_id, status="patch_ready"),
        )

    session = runtime.start_agent_session(run_id, tasks[0].candidate_id, {"goal": "try worker"})
    context = runtime.get_agent_context(session.agent_session_id)
    assert context["budget"]["max_wall_seconds"] <= 120
    assert context["budget"]["max_verifier_runs"] == 3
    assert context["budget"]["deadline_at"].endswith("Z")
    assert context["candidate_task"]["candidate_id"] == tasks[0].candidate_id
    runtime.submit_candidate(
        run_id,
        tasks[0].candidate_id,
        ArtifactBundle(
            candidate_id=tasks[0].candidate_id,
            status="patch_ready",
            agent_session_id=session.agent_session_id,
        ),
    )
    record = runtime._load_candidate_record(run_id, tasks[0].candidate_id)
    assert record.status == "submitted"


def test_agent_session_pool_status_observation_and_wait_loop(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec_data = spec_for(project, max_candidates=4).model_dump(mode="json")
    spec_data["budget"]["max_parallel"] = 2
    frozen = runtime.freeze_spec(SearchSpec.model_validate(spec_data), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    tasks = runtime.next_batch(run_id, 3)

    first = runtime.start_agent_session(
        run_id,
        tasks[0].candidate_id,
        {"goal": "try first"},
        budget={"max_wall_seconds": 120, "max_steps": 2, "max_tool_calls": 4},
    )
    second = runtime.start_agent_session(run_id, tasks[1].candidate_id, "try second")

    run_suffix = run_id.removeprefix("run_")
    assert first.agent_session_id == f"agent_{run_suffix}_001"
    assert first.budget.max_wall_seconds <= 120
    assert second.agent_session_id == f"agent_{run_suffix}_002"
    assert runtime._active_agent_session_count(run_id) == 2

    with pytest.raises(RuntimeError, match="pool is full"):
        runtime.start_agent_session(run_id, tasks[2].candidate_id)

    context = runtime.get_agent_context(first.agent_session_id)
    assert context["agent_session_id"] == first.agent_session_id
    assert context["candidate_task"]["candidate_id"] == tasks[0].candidate_id
    assert context["peer_status"][0]["agent_session_id"] == second.agent_session_id

    updated = runtime.update_agent_status(
        first.agent_session_id,
        phase="implementing",
        current_goal="edit initializer",
        last_action="read workspace",
        next_step="make one patch",
    )
    assert updated.phase == "implementing"
    assert updated.current_goal == "edit initializer"

    observation = runtime.publish_observation(
        first.agent_session_id,
        summary="first direction leaves gaps",
        evidence="static inspection",
        next_ideas=["seed corners"],
        tags=["layout"],
    )
    observations = runtime.list_observations(run_id, tags=["layout"])
    assert observations[0]["observation_id"] == observation.observation_id
    assert observations[0]["next_ideas"] == ["seed corners"]

    stepped = runtime.record_agent_step(first.agent_session_id, steps_delta=2, tool_calls_delta=1)
    assert stepped.status == "finalizing"
    assert stepped.counters["steps"] == 2

    completed = runtime.finish_agent_session(second.agent_session_id, summary="second done")
    assert completed.status == "completed"
    wait = runtime.wait_agent_events(run_id, timeout_seconds=0)
    assert wait.timed_out is False
    assert wait.last_event_id is not None
    assert any(event.type == "agent_completed" for event in wait.events)
    repeated_wait = runtime.wait_agent_events(
        run_id,
        timeout_seconds=0,
        since_event_id=wait.last_event_id,
    )
    assert repeated_wait.events == []
    assert repeated_wait.last_event_id == wait.last_event_id

    third = runtime.start_agent_session(run_id, tasks[2].candidate_id)
    assert third.agent_session_id == f"agent_{run_suffix}_003"
    assert runtime._active_agent_session_count(run_id) == 2


def test_agent_session_ids_are_unique_across_runs(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])

    first_run_id = runtime.create_run(frozen.frozen_spec_id)
    second_run_id = runtime.create_run(frozen.frozen_spec_id)
    first_task = runtime.next_batch(first_run_id, 1)[0]
    second_task = runtime.next_batch(second_run_id, 1)[0]

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
    first_task = runtime.next_batch(first_run_id, 1)[0]
    second_task = runtime.next_batch(second_run_id, 1)[0]
    first = runtime.start_agent_session(first_run_id, first_task.candidate_id)
    second = runtime.start_agent_session(second_run_id, second_task.candidate_id)

    legacy_first = first.model_copy(update={"agent_session_id": "agent_001"})
    legacy_second = second.model_copy(update={"agent_session_id": "agent_001"})
    runtime._write_agent_session(legacy_first)
    runtime._write_agent_session(legacy_second)

    with pytest.raises(RuntimeError, match="ambiguous agent_session_id"):
        runtime.get_agent_context("agent_001")


def test_agent_session_abort_and_run_deadline_enforcement(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=2), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    task = runtime.next_batch(run_id, 1)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)

    timeout_wait = runtime.wait_agent_events(run_id, timeout_seconds=0, since_event_id="event_999999")
    assert timeout_wait.timed_out is True
    assert timeout_wait.active_count == 1

    aborted = runtime.abort_all_agent_sessions(run_id, "stop search")
    assert [item.status for item in aborted] == ["aborted"]
    assert runtime._active_agent_session_count(run_id) == 0
    abort_wait = runtime.wait_agent_events(run_id, timeout_seconds=0)
    assert any(event.type == "agent_aborted" for event in abort_wait.events)

    run = runtime._load_run(run_id)
    run.created_at = "2000-01-01T00:00:00Z"
    runtime._write_run(run)
    with pytest.raises(RuntimeError, match="run budget exhausted"):
        runtime.start_agent_session(run_id, task.candidate_id)
    assert runtime.wait_agent_events(run_id, timeout_seconds=0).run_deadline_reached is True


def test_submit_candidate_validates_agent_session_provenance(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "AnySearchAgent",
        },
        max_candidates=2,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    tasks = runtime.next_batch(run_id, 2)
    session = runtime.start_agent_session(run_id, tasks[0].candidate_id, "try one concrete variant")

    with pytest.raises(ValueError, match="agent_session_id"):
        runtime.submit_candidate(
            run_id,
            tasks[0].candidate_id,
            ArtifactBundle(
                candidate_id=tasks[0].candidate_id,
                status="patch_ready",
            ),
        )

    with pytest.raises(ValueError, match="does not belong"):
        runtime.submit_candidate(
            run_id,
            tasks[1].candidate_id,
            ArtifactBundle(
                candidate_id=tasks[1].candidate_id,
                status="patch_ready",
                agent_session_id=session.agent_session_id,
            ),
        )

    runtime.submit_candidate(
        run_id,
        tasks[0].candidate_id,
        ArtifactBundle(
            candidate_id=tasks[0].candidate_id,
            status="patch_ready",
            agent_session_id=session.agent_session_id,
            summary="implemented the dispatched idea",
            next_ideas=["try a smaller mutation next"],
        ),
    )
    record = runtime._load_candidate_record(run_id, tasks[0].candidate_id)
    assert record.artifact.agent_session_id == session.agent_session_id


def test_submit_candidate_rejects_aborted_agent_session(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "AnySearchAgent",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    task = runtime.next_batch(run_id, 1)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id, "try one concrete variant")
    runtime.abort_agent_session(session.agent_session_id, "deadline exceeded")

    with pytest.raises(RuntimeError, match="aborted agent session"):
        runtime.submit_candidate(
            run_id,
            task.candidate_id,
            ArtifactBundle(
                candidate_id=task.candidate_id,
                status="patch_ready",
                agent_session_id=session.agent_session_id,
            ),
        )


def test_history_and_report_include_agent_sessions(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "AnySearchAgent",
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    task = runtime.next_batch(run_id, 1)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id, {"goal": "document session"})
    runtime.submit_candidate(
        run_id,
        task.candidate_id,
        ArtifactBundle(
            candidate_id=task.candidate_id,
            status="patch_ready",
            agent_session_id=session.agent_session_id,
            summary="session-aware candidate",
            next_ideas=["inspect report linkage"],
        ),
    )
    runtime.finish_agent_session(session.agent_session_id, summary="session completed")

    history = runtime.list_history(run_id)
    candidate = history["candidates"][0]
    assert candidate["agent_sessions"][0]["agent_session_id"] == session.agent_session_id
    assert candidate["artifact_agent_session_id"] == session.agent_session_id
    assert candidate["next_ideas"] == ["inspect report linkage"]

    report_path = runtime.report(run_id)
    report = report_path.read_text(encoding="utf-8")
    assert "## Agent Sessions" in report
    assert session.agent_session_id in report
    assert "session completed" in report


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
