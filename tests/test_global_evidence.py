from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

import pytest

from goal_plus.evidence_annotator import (
    EvidenceComparisonOutput,
    EvidenceComparisonResult,
    drain_evidence_annotations,
)
from goal_plus.models import (
    EvidenceViewRecord,
    SearchSpec,
    SupplementalEvaluation,
)
from goal_plus.runtime import (
    EXTERNAL_EVIDENCE_DIR_ENV,
    FileSearchRuntime,
    SUPPLEMENTAL_EVALUATION_ENABLED_ENV,
)
from goal_plus.tools import SearchTools
from tests._runtime_helpers import git_commit_all, make_project, spec_for


def _search_with_candidates(
    tmp_path: Path,
    count: int,
    *,
    strategy_updates: dict | None = None,
) -> tuple[FileSearchRuntime, str, list[tuple[str, str, Path]]]:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "value = Path('initial_program.py').read_text().split('=', 1)[1].strip()\n"
        "print(json.dumps({'combined_score': float(value)}))\n",
        encoding="utf-8",
    )
    spec_data = spec_for(project, max_parallel=count).model_dump(mode="json")
    spec_data["workspace"] = {"backend": "git_worktree"}
    spec_data["strategy"].update(strategy_updates or {})
    runtime = FileSearchRuntime(tmp_path / ".gp")
    frozen = runtime.freeze_spec(
        SearchSpec.model_validate(spec_data),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    search_plan = runtime.plan_next(run_id, requested_k=count)
    tasks = runtime.start_batch(run_id, search_plan.plan_id)
    candidates = []
    for task in tasks:
        session = runtime.start_agent_session(run_id, task.candidate_id)
        candidates.append(
            (task.candidate_id, session.agent_session_id, task.workspace)
        )
    return runtime, run_id, candidates


def _git(workspace: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=workspace, text=True).strip()


def _complete_supplemental_view(
    runtime: FileSearchRuntime,
    run_id: str,
    candidate_id: str,
    iteration: int,
    observations: list[dict],
) -> None:
    task = runtime._load_evidence_annotation_task(run_id, candidate_id, iteration)
    assert task is not None
    runtime._write_evidence_annotation_task(
        task.model_copy(
            update={
                "state": "completed",
                "view": EvidenceViewRecord(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    iteration=iteration,
                    attempt_commit=task.attempt_commit,
                    description=f"Observed candidate {candidate_id}.",
                    supplemental_evaluation=SupplementalEvaluation.model_validate(
                        {"observations": observations}
                    ),
                    created_at="2026-01-01T00:00:00Z",
                ),
            }
        )
    )


def test_global_evidence_is_immediate_and_view_is_late_bound(tmp_path: Path) -> None:
    runtime, run_id, candidates = _search_with_candidates(tmp_path, 2)
    first, second = candidates
    assert not runtime.should_inject_global_evidence_after_verifier(run_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(
            pool.map(runtime.get_global_evidence, [first[1], second[1]])
        ) == [[], []]

    for (_, session_id, workspace), value, hypothesis in zip(
        candidates,
        (1, 2),
        ("Raise the first value", "Raise the second value"),
        strict=True,
    ):
        (workspace / "initial_program.py").write_text(
            f"VALUE = {value}\n", encoding="utf-8"
        )
        report = runtime.run_verifier(
            run_id,
            runtime._load_agent_session_by_id(session_id).candidate_id,
            agent_session_id=session_id,
            hypothesis=hypothesis,
        )
        assert report.disposition == "keep"

    (second[2] / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    discarded = runtime.run_verifier(
        run_id,
        second[0],
        agent_session_id=second[1],
        hypothesis="Replace the second value with a smaller constant",
    )
    assert discarded.disposition == "discard"

    view = runtime.get_global_evidence(first[1])
    assert [(entry["candidate_id"], entry["iteration"]) for entry in view] == [
        (first[0], 1),
        (second[0], 1),
        (second[0], 2),
    ]
    assert [entry["score"] for entry in view] == [1.0, 2.0, 1.0]
    assert [entry["disposition"] for entry in view] == [
        "keep",
        "keep",
        "discard",
    ]
    assert all(entry["commit"] and entry["view"] is None for entry in view)
    assert all("supplemental_available" not in entry for entry in view)

    discarded_commit = view[-1]["commit"]
    annotation_task = runtime._load_evidence_annotation_task(run_id, second[0], 2)
    assert annotation_task is not None
    runtime._write_evidence_annotation_task(
        annotation_task.model_copy(
            update={
                "state": "completed",
                "view": EvidenceViewRecord(
                    run_id=run_id,
                    candidate_id=second[0],
                    iteration=2,
                    attempt_commit=discarded_commit,
                    description=(
                        "Changed the candidate value from two to one without altering "
                        "the evaluator."
                    ),
                    created_at="2026-01-01T00:00:00Z",
                ),
            }
        )
    )
    completed = runtime.get_global_evidence(first[1])
    assert completed[-1]["view"] == (
        "Changed the candidate value from two to one without altering the evaluator."
    )
    assert completed[-1]["view_created_at"] == "2026-01-01T00:00:00Z"
    read = runtime._load_agent_session_by_id(first[1]).global_evidence_reads[-1]
    assert read.evidence_count == 3
    assert read.completed_view_count == 1
    assert read.completed_supplemental_evaluation_count == 0
    assert read.completed_views[0].candidate_id == second[0]
    assert read.completed_views[0].iteration == 2
    assert read.completed_views[0].commit == discarded_commit
    assert read.completed_views[0].view_created_at == "2026-01-01T00:00:00Z"
    assert (second[2] / "initial_program.py").read_text(encoding="utf-8") == (
        "VALUE = 2\n"
    )

    peer_commit = view[1]["commit"]
    subprocess.run(
        ["git", "cat-file", "-e", f"{peer_commit}^{{commit}}"],
        cwd=first[2],
        check=True,
    )
    assert _git(first[2], "show", f"{peer_commit}:initial_program.py") == "VALUE = 2"


def test_external_evaluation_attaches_only_to_its_exact_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run_id, [candidate] = _search_with_candidates(tmp_path, 1)
    candidate_id, session_id, workspace = candidate
    (workspace / "initial_program.py").write_text("VALUE = 3\n", encoding="utf-8")
    report = runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Raise the candidate value",
    )
    feedback_path = tmp_path / "evaluations" / "auto-1.json"
    feedback_path.parent.mkdir()
    monkeypatch.setenv(EXTERNAL_EVIDENCE_DIR_ENV, str(feedback_path.parent))
    payload = {
        "source": "edgebench",
        "artifact": {
            "source": "goal_plus_best",
            "run_id": run_id,
            "candidate_id": candidate_id,
            "iteration": report.best_iteration,
            "commit": report.best_git_head,
            "local_score": 3.0,
        },
        "evaluation": {
            "round_id": "auto-1",
            "status": "completed",
            "valid": True,
            "score": 42,
            "score_0_100": 73.5,
            "summary": "Official evaluation completed",
        },
    }
    feedback_path.write_text(json.dumps(payload), encoding="utf-8")

    [entry] = runtime.get_global_evidence(session_id)
    assert entry["external_evaluations"] == [
        {**payload["evaluation"], "source": "edgebench"}
    ]

    payload["artifact"]["commit"] = "stale-commit"
    feedback_path.write_text(json.dumps(payload), encoding="utf-8")
    [entry] = runtime.get_global_evidence(session_id)
    assert "external_evaluations" not in entry


def test_independent_mode_does_not_leak_external_evaluation_to_peers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run_id, candidates = _search_with_candidates(
        tmp_path,
        2,
        strategy_updates={"config": {"global_evidence_mode": "independent"}},
    )
    reports = []
    for candidate_id, session_id, workspace in candidates:
        (workspace / "initial_program.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        reports.append(
            runtime.run_verifier(
                run_id,
                candidate_id,
                agent_session_id=session_id,
                hypothesis="Set the candidate value",
            )
        )

    feedback_path = tmp_path / "evaluations" / "auto-1.json"
    feedback_path.parent.mkdir()
    monkeypatch.setenv(EXTERNAL_EVIDENCE_DIR_ENV, str(feedback_path.parent))
    feedback_path.write_text(
        json.dumps(
            {
                "source": "edgebench",
                "artifact": {
                    "source": "goal_plus_best",
                    "run_id": run_id,
                    "candidate_id": candidates[1][0],
                    "iteration": reports[1].best_iteration,
                    "commit": reports[1].best_git_head,
                },
                "evaluation": {"round_id": "auto-1", "score": 42},
            }
        ),
        encoding="utf-8",
    )

    first_view = runtime.get_global_evidence(candidates[0][1])
    second_view = runtime.get_global_evidence(candidates[1][1])
    assert all("external_evaluations" not in entry for entry in first_view)
    assert second_view[0]["external_evaluations"][0]["score"] == 42


@pytest.mark.parametrize("mode", ["auto", "independent"])
def test_post_verifier_injection_respects_global_evidence_mode(
    tmp_path: Path,
    mode: str,
) -> None:
    runtime, run_id, candidates = _search_with_candidates(
        tmp_path,
        2,
        strategy_updates={"config": {"global_evidence_mode": mode}},
    )
    reports = []
    for candidate_id, session_id, workspace in candidates:
        (workspace / "initial_program.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        reports.append(
            SearchTools(runtime).search_run_verifier(
                run_id,
                candidate_id,
                agent_session_id=session_id,
                hypothesis="Set the candidate value",
            )
        )

    explicit_candidates = [
        [entry["candidate_id"] for entry in runtime.get_global_evidence(session_id)]
        for _, session_id, _ in candidates
    ]
    expected_explicit = (
        [[candidates[0][0], candidates[1][0]]] * 2
        if mode == "auto"
        else [[candidates[0][0]], [candidates[1][0]]]
    )
    assert explicit_candidates == expected_explicit
    if mode == "independent":
        assert all("global_evidence_injected" not in report for report in reports)
        return

    visible_candidates = [
        [entry["candidate_id"] for entry in report["global_evidence_snapshot"]]
        for report in reports
    ]
    expected_injected = [
        [candidates[0][0]],
        [candidates[0][0], candidates[1][0]],
    ]
    assert visible_candidates == expected_injected
    assert [report["global_evidence_entry_count"] for report in reports] == [1, 2]


def test_global_evidence_presents_open_evaluation_without_peer_basis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SUPPLEMENTAL_EVALUATION_ENABLED_ENV, "1")
    runtime, run_id, candidates = _search_with_candidates(tmp_path, 2)
    first, second = candidates
    goal_path = runtime.root_dir / "goal-plus" / "gp_test" / "goal.json"
    goal_path.parent.mkdir(parents=True)
    goal_path.write_text(
        json.dumps(
            {
                "raw_goal": "Fix the public cache invalidation issue.",
                "goal_revision": 1,
                "goal_revisions": [
                    {
                        "revision": 1,
                        "raw_goal": "Fix the public cache invalidation issue.",
                    }
                ],
                "search_tasks": [{"goal_revision": 1, "run_id": run_id}],
            }
        ),
        encoding="utf-8",
    )
    for candidate, value, hypothesis in (
        (first, 1, "Use the direct implementation"),
        (first, 3, "Improve the direct implementation"),
        (second, 2, "Use the cached implementation"),
    ):
        candidate_id, session_id, workspace = candidate
        (workspace / "initial_program.py").write_text(
            f"VALUE = {value}\n", encoding="utf-8"
        )
        report = runtime.run_verifier(
            run_id,
            candidate_id,
            agent_session_id=session_id,
            hypothesis=hypothesis,
        )
        assert report.disposition == "keep"

    first_task = runtime._load_evidence_annotation_task(run_id, first[0], 1)
    task = runtime._load_evidence_annotation_task(run_id, second[0], 1)
    assert first_task is not None
    assert task is not None and task.supplemental_evaluation_enabled is True
    task_payload = task.model_dump(mode="json")
    assert "comparison_basis" not in task_payload
    assert "task_context" not in task_payload
    assert task.task_context_source == "goal_plus_raw_goal"
    assert task.task_context_ref == "goal_plus:gp_test:revision:1"
    assert len(task.task_context_sha256 or "") == 64

    context = runtime._evidence_annotation_context(run_id, second[0], 1)
    assert "acceptance_contract" not in context
    assert context["task_context"] == "Fix the public cache invalidation issue."
    assert context["task_context_source"] == "goal_plus_raw_goal"
    assert context["supplemental_evaluation_enabled"] is True
    assert context["changed_files"] == ["initial_program.py"]
    assert context["verifier_contract"][0]["role"] == "ranking_signal"
    assert context["verifier_contract"][0]["command"][-1] == "evaluator.py"
    assert "comparison_basis" not in context
    assert "peer_evidence" not in context

    runtime._write_evidence_annotation_task(
        task.model_copy(
            update={
                "state": "completed",
                "view": EvidenceViewRecord(
                    run_id=run_id,
                    candidate_id=second[0],
                    iteration=1,
                    attempt_commit=task.attempt_commit,
                    description="Changed the implementation to use a cached value.",
                    supplemental_evaluation=SupplementalEvaluation.model_validate(
                        {
                            "observations": [
                                {
                                    "state": "supported",
                                    "label": "Cache coherence",
                                    "text": "The diff introduces a cache without an invalidation path.",
                                    "evidence": [
                                        {
                                            "source": "candidate_diff",
                                            "locator": "initial_program.py",
                                            "excerpt": "VALUE is read through the new cache.",
                                        }
                                    ],
                                },
                                {
                                    "state": "unresolved",
                                    "label": "Hidden evaluator coverage",
                                    "text": "No hidden evaluator evidence is available.",
                                    "evidence": [
                                        {
                                            "source": "evidence_scope",
                                            "locator": "visible verifier results",
                                            "excerpt": "Only the visible evaluator was run.",
                                        }
                                    ],
                                },
                            ]
                        }
                    ),
                    created_at="2026-01-01T00:00:00Z",
                ),
            }
        )
    )

    entries = runtime.get_global_evidence(second[1])
    entry = next(item for item in entries if item["candidate_id"] == second[0])
    assert "task_context" not in entry
    assert "task_context_source" not in entry
    assert entry["score"] == 2.0
    assert entry["supplemental_available"] is True
    assert "supplemental_evaluation" not in entry

    detail = runtime.get_evidence_detail(first[1], second[0], 1)
    assert detail["schema_version"] == 2
    assert detail["commit"] == task.attempt_commit
    observations = detail["supplemental_evaluation"]["observations"]
    assert observations[0]["observation_ordinal"] == 1
    assert observations[0]["topic_id"].startswith("topic_")
    assert observations[0]["label"] == "Cache coherence"
    assert observations[1]["state"] == "unresolved"
    assert "summary" not in detail["supplemental_evaluation"]
    assert "comparisons" not in detail["supplemental_evaluation"]

    history = runtime.list_global_evidence(
        second[1], candidate_id=second[0], limit=20
    )
    reference = history["items"][0]
    assert reference["shared_tool_publish_status"] == "not_staged"
    expanded = runtime.get_global_evidence_entry(
        second[1],
        reference["candidate_id"],
        reference["iteration"],
        reference["commit"],
    )
    assert expanded["supplemental_available"] is True
    assert "supplemental_evaluation" not in expanded

    completed_task = runtime._load_evidence_annotation_task(run_id, second[0], 1)
    assert completed_task is not None and completed_task.view is not None
    runtime._write_evidence_annotation_task(
        completed_task.model_copy(
            update={
                "state": "completed",
                "view": completed_task.view.model_copy(
                    update={"attempt_commit": "stale"}
                ),
            }
        )
    )
    with pytest.raises(RuntimeError, match="does not match iteration"):
        runtime.get_evidence_detail(first[1], second[0], 1)


def test_comparison_annotator_automatically_selects_bounded_peer_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SUPPLEMENTAL_EVALUATION_ENABLED_ENV, "1")
    runtime, run_id, candidates = _search_with_candidates(tmp_path, 2)
    for index, (candidate_id, session_id, workspace) in enumerate(
        candidates,
        start=1,
    ):
        (workspace / "initial_program.py").write_text(
            f"VALUE = {index}\n",
            encoding="utf-8",
        )
        report = runtime.run_verifier(
            run_id,
            candidate_id,
            agent_session_id=session_id,
            hypothesis=f"Exercise strategy {index}",
        )
        assert report.aggregate_score == float(index)
        _complete_supplemental_view(
            runtime,
            run_id,
            candidate_id,
            1,
            [
                {
                    "state": "supported",
                    "label": "Implementation strategy",
                    "text": f"Candidate {index} uses strategy {index}.",
                    "evidence": [
                        {
                            "source": "candidate_diff",
                            "locator": "initial_program.py",
                            "excerpt": f"VALUE = {index}",
                        }
                    ],
                }
            ],
        )

    class AutomaticComparisonAnnotator:
        contexts: list[dict] = []

        def compare(self, context: dict) -> EvidenceComparisonResult:
            self.contexts.append(context)
            serialized = json.dumps(context["comparison_catalog"], sort_keys=True)
            assert '"score"' not in serialized
            assert '"evidence"' not in serialized
            target_identity = context["comparison_catalog"]["target"]
            observations = [
                observation
                for view in context["comparison_catalog"]["views"]
                for observation in view["observations"]
            ]
            target = next(
                item
                for item in observations
                if all(
                    item["reference"][field] == target_identity[field]
                    for field in ("candidate_id", "iteration", "commit")
                )
            )
            peer = next(
                item
                for item in observations
                if item["reference"]["candidate_id"]
                != target_identity["candidate_id"]
            )
            references = [target["reference"], peer["reference"]]
            return EvidenceComparisonResult(
                output=EvidenceComparisonOutput.model_validate(
                    {
                        "gist": (
                            f"{target_identity['candidate_id']} 与 peer 采用不同的可见实现策略。"
                        ),
                        "selections": [
                            {
                                "reference": reference,
                                "reason": "覆盖当前实现与一个可核对的 peer 对照。",
                            }
                            for reference in references
                        ],
                        "agreements": [],
                        "differences": [
                            {
                                "text": "两条 observation 描述了不同实现策略。",
                                "observation_refs": references,
                            }
                        ],
                        "unique_observations": [],
                        "unresolved": [],
                    }
                ),
                usage={"input_tokens": 11, "output_tokens": 7},
            )

    annotator = AutomaticComparisonAnnotator()
    assert drain_evidence_annotations(
        runtime.root_dir,
        run_id,
        annotator=annotator,
    ) == 0
    assert len(annotator.contexts) == 2
    assert all(
        context["selection_contract"]["score_available"] is False
        and context["selection_contract"]["must_include_target_view"] is True
        for context in annotator.contexts
    )

    for candidate_id, _, _ in candidates:
        task = runtime._load_evidence_annotation_task(run_id, candidate_id, 1)
        assert task is not None
        assert task.state == "completed"
        assert task.comparison_state == "completed"
        assert task.comparison_attempts == 1
        assert task.comparison is not None
        assert len(task.comparison.selections) == 2
        assert task.comparison.catalog_view_count == 2

    entries = runtime.get_global_evidence(candidates[0][1])
    assert all(entry["comparison_available"] is True for entry in entries)
    assert all(entry["comparison_gist"] for entry in entries)
    assert all("selections" not in entry for entry in entries)
    detail = runtime.get_evidence_detail(
        candidates[0][1],
        candidates[1][0],
        1,
    )
    assert detail["comparison"]["gist"]
    assert len(detail["comparison"]["selections"]) == 2
    assert runtime.list_iterations(run_id, candidates[1][0])[0]["score"] == 2.0


def test_supplemental_capability_and_detail_respect_disabled_and_independent_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run_id, candidates = _search_with_candidates(
        tmp_path,
        2,
        strategy_updates={"config": {"global_evidence_mode": "independent"}},
    )
    first, second = candidates
    disabled = runtime.get_agent_context(first[1])
    assert disabled["supplemental_evaluation_enabled"] is False
    with pytest.raises(RuntimeError, match="disabled"):
        runtime.get_evidence_detail(first[1], first[0], 1)

    monkeypatch.setenv(SUPPLEMENTAL_EVALUATION_ENABLED_ENV, "1")
    enabled = runtime.get_agent_context(first[1])
    assert enabled["supplemental_evaluation_enabled"] is True
    with pytest.raises(PermissionError, match="caller's candidate"):
        runtime.get_evidence_detail(first[1], second[0], 1)


def test_worker_hypothesis_is_required_and_parent_evidence_is_private(
    tmp_path: Path,
) -> None:
    runtime, run_id, [candidate] = _search_with_candidates(tmp_path, 1)
    candidate_id, session_id, workspace = candidate

    runtime.run_verifier(run_id, candidate_id, hypothesis="parent verification")
    assert runtime.get_global_evidence(session_id) == []

    program = workspace / "initial_program.py"
    program.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="requires a non-empty hypothesis"):
        runtime.run_verifier(
            run_id,
            candidate_id,
            agent_session_id=session_id,
        )
    assert len(runtime.list_iterations(run_id, candidate_id)) == 1

    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="  Index the candidate value once  ",
    )
    iterations = runtime.list_iterations(run_id, candidate_id)
    assert iterations[-1]["hypothesis"] == "Index the candidate value once"
    assert runtime.get_global_evidence(session_id)[0]["commit"] == (
        iterations[-1]["git_head"]
    )

    runtime.select(run_id)
    runtime.promote(run_id, candidate_id)
    before = runtime.get_global_evidence(session_id)
    with pytest.raises(RuntimeError, match="state promoted"):
        runtime.run_verifier(
            run_id,
            candidate_id,
            agent_session_id=session_id,
            hypothesis="Mutate after promotion",
        )
    assert runtime.get_global_evidence(session_id) == before


def test_global_evidence_index_stays_bounded_after_101_rounds_per_candidate() -> None:
    archive = [
        {
            "candidate_id": f"c{candidate:03d}",
            "iteration": iteration,
            "commit": f"{candidate:02d}{iteration:04d}",
            "score": float(100 if iteration == 50 else iteration % 10),
            "disposition": "keep" if iteration == 50 else "retain",
            "view": f"candidate {candidate} iteration {iteration}",
            "view_created_at": "2026-08-13T00:00:00Z",
            "supplemental_evaluation": None,
            "shared_tools": [],
        }
        for candidate in range(1, 5)
        for iteration in range(1, 102)
    ]

    index = FileSearchRuntime._global_evidence_index(
        archive,
        metric_direction="maximize",
    )

    assert len(index) == 8
    assert {entry["representative_role"] for entry in index} == {
        "hard_best",
        "latest_settled",
    }
    assert {entry["candidate_evidence_count"] for entry in index} == {101}
    assert {entry["candidate_omitted_count"] for entry in index} == {99}
    assert {
        entry["iteration"]
        for entry in index
        if entry["representative_role"] == "hard_best"
    } == {50}
    assert {
        entry["iteration"]
        for entry in index
        if entry["representative_role"] == "latest_settled"
    } == {101}


def test_global_evidence_index_preserves_shared_tool_publication_rows() -> None:
    archive = [
        {
            "candidate_id": "c001",
            "iteration": iteration,
            "commit": f"commit-{iteration}",
            "score": float(iteration),
            "disposition": "keep",
            "view": f"iteration {iteration}",
            "view_created_at": "2026-08-13T00:00:00Z",
            "supplemental_evaluation": None,
            "shared_tools": (
                [{"tool_id": "tool-2", "snapshot_hash": "hash-2"}]
                if iteration == 2
                else []
            ),
        }
        for iteration in range(1, 5)
    ]

    index = FileSearchRuntime._global_evidence_index(
        archive,
        metric_direction="maximize",
    )

    assert [entry["iteration"] for entry in index] == [2, 4]
    assert [entry["representative_role"] for entry in index] == [
        "shared_dir_settlement",
        "hard_best_and_latest",
    ]
    assert index[0]["shared_tools"] == archive[1]["shared_tools"]


def test_global_evidence_hard_best_excludes_discarded_scores() -> None:
    archive = [
        {
            "candidate_id": "c001",
            "iteration": 1,
            "commit": "kept",
            "score": 1.0,
            "disposition": "keep",
            "view": "kept view",
            "view_created_at": "2026-08-13T00:00:00Z",
            "supplemental_evaluation": None,
            "shared_tools": [],
        },
        {
            "candidate_id": "c001",
            "iteration": 2,
            "commit": "discarded",
            "score": 100.0,
            "disposition": "discard",
            "view": "discarded view",
            "view_created_at": "2026-08-13T00:00:01Z",
            "supplemental_evaluation": None,
            "shared_tools": [],
        },
    ]

    index = FileSearchRuntime._global_evidence_index(
        archive,
        metric_direction="maximize",
    )

    assert [entry["representative_role"] for entry in index] == [
        "hard_best",
        "latest_settled",
    ]
    assert index[0]["commit"] == "kept"
    assert index[1]["commit"] == "discarded"


def test_annotator_config_overrides_then_inherits_worker_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GOAL_PLUS_EVIDENCE_ANNOTATOR_BASE_URL",
        "http://proxy.example/v1",
    )
    runtime, run_id, [candidate] = _search_with_candidates(
        tmp_path,
        1,
        strategy_updates={
            "worker_launch": {
                "model": "worker-model",
                "reasoning_effort": "high",
            },
            "evidence_annotator": {
                "reasoning_effort": "low",
                "timeout_seconds": 90,
            },
        },
    )
    candidate_id, session_id, workspace = candidate
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Set the candidate value",
    )

    task = runtime._load_evidence_annotation_task(run_id, candidate_id, 1)
    assert task is not None and task.profile is not None
    assert task.profile.host == "codex"
    assert task.profile.model == "worker-model"
    assert task.profile.reasoning_effort == "low"
    assert task.profile.timeout_seconds == 90
    assert task.profile.provider is not None
    assert task.profile.provider.base_url is None
    assert task.profile.provider.base_url_env == (
        "GOAL_PLUS_EVIDENCE_ANNOTATOR_BASE_URL"
    )
    assert "proxy.example" not in task.model_dump_json()

    session = runtime._load_agent_session_by_id(session_id)
    runtime._write_agent_session(
        session.model_copy(update={"launch": {"continuation": "native_session"}})
    )
    context = runtime._evidence_annotation_context(run_id, candidate_id, 1)
    assert context["annotator"]["model"] == "worker-model"
    assert context["annotator"]["reasoning_effort"] == "low"

    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Verify without changing candidate files",
    )
    continued_context = runtime._evidence_annotation_context(
        run_id, candidate_id, 2
    )
    assert continued_context["actual_diff"] == ""
    assert "+VALUE = 1" in continued_context["candidate_diff"]
    assert continued_context["candidate_changed_files"] == ["initial_program.py"]
    assert continued_context["annotator"]["model"] == "worker-model"


def test_pi_worker_model_is_inherited_by_pi_annotator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pi_home = tmp_path / "pi-home"
    pi_home.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi_home))
    monkeypatch.setenv("PI_PROVIDER", "bench-openai")
    runtime, run_id, [candidate] = _search_with_candidates(
        tmp_path,
        1,
        strategy_updates={
            "worker_host": "pi-rpc",
            "worker_budget": {"max_runtime_seconds": 60},
            "worker_launch": {
                "model": "gpt-test",
                "reasoning_effort": "high",
            },
        },
    )
    candidate_id, session_id, workspace = candidate
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Set the Pi candidate value",
    )

    task = runtime._load_evidence_annotation_task(run_id, candidate_id, 1)
    assert task is not None and task.profile is not None
    assert task.state == "pending"
    assert task.profile.host == "pi-rpc"
    assert task.profile.model == "gpt-test"
    assert task.profile.pi_provider == "bench-openai"
    assert task.profile.reasoning_effort == "high"
    assert task.profile.pi_home == str(pi_home)
    assert task.profile.codex_home is None
    assert task.profile.provider is None
    context = runtime._evidence_annotation_context(run_id, candidate_id, 1)
    assert context["annotator"]["pi_provider"] == "bench-openai"


def test_annotator_environment_overrides_pi_worker_model_and_stale_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pi_home = tmp_path / "pi-home"
    pi_home.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi_home))
    monkeypatch.setenv("PI_PROVIDER", "deepseek")
    monkeypatch.setenv(
        "GOAL_PLUS_EVIDENCE_ANNOTATOR_MODEL",
        "bench-openai/gpt-5.6-sol",
    )
    monkeypatch.setenv(
        "GOAL_PLUS_EVIDENCE_ANNOTATOR_REASONING_EFFORT",
        "medium",
    )
    runtime, run_id, [candidate] = _search_with_candidates(
        tmp_path,
        1,
        strategy_updates={
            "worker_host": "pi-rpc",
            "worker_budget": {"max_runtime_seconds": 60},
            "worker_launch": {
                "model": "deepseek/deepseek-v4-flash",
                "reasoning_effort": "high",
            },
            "evidence_annotator": {"pi_provider": "pi-rpc"},
        },
    )
    candidate_id, session_id, workspace = candidate
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Set the Pi candidate value",
    )

    task = runtime._load_evidence_annotation_task(run_id, candidate_id, 1)
    assert task is not None and task.profile is not None
    assert task.profile.host == "pi-rpc"
    assert task.profile.model == "bench-openai/gpt-5.6-sol"
    assert task.profile.pi_provider == "bench-openai"
    assert task.profile.reasoning_effort == "medium"


def test_pi_worker_can_use_an_independent_codex_annotator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv(
        "GOAL_PLUS_EVIDENCE_ANNOTATOR_BASE_URL",
        "http://proxy.example/v1",
    )
    runtime, run_id, [candidate] = _search_with_candidates(
        tmp_path,
        1,
        strategy_updates={
            "worker_host": "pi-rpc",
            "worker_budget": {"max_runtime_seconds": 60},
            "worker_launch": {
                "model": "worker-model",
                "reasoning_effort": "high",
            },
            "evidence_annotator": {
                "host": "codex",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "medium",
            },
        },
    )
    candidate_id, session_id, workspace = candidate
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Set the Pi candidate value",
    )

    task = runtime._load_evidence_annotation_task(run_id, candidate_id, 1)
    assert task is not None and task.profile is not None
    assert task.profile.host == "codex"
    assert task.profile.model == "gpt-5.6-luna"
    assert task.profile.reasoning_effort == "medium"
    assert task.profile.codex_home == str(codex_home)
    assert task.profile.pi_home is None
    assert task.profile.pi_provider is None
    assert task.profile.provider is not None


def test_pi_annotator_inherits_host_provider_and_model_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pi_home = tmp_path / "pi-home"
    pi_home.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi_home))
    monkeypatch.setenv("PI_PROVIDER", "glm-proxy")
    monkeypatch.setenv("PI_MODEL", "GLM-5.2")
    runtime, run_id, [candidate] = _search_with_candidates(
        tmp_path,
        1,
        strategy_updates={
            "worker_host": "pi-rpc",
            "worker_budget": {"max_runtime_seconds": 60},
        },
    )
    candidate_id, session_id, workspace = candidate
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Set the Pi candidate value",
    )

    task = runtime._load_evidence_annotation_task(run_id, candidate_id, 1)
    assert task is not None and task.profile is not None
    assert task.profile.model == "GLM-5.2"
    assert task.profile.pi_provider == "glm-proxy"


@pytest.mark.parametrize(
    "annotator_config",
    [
        {"model": "deepseek/deepseek-chat", "reasoning_effort": "low"},
        {
            "model": "deepseek-chat",
            "pi_provider": "deepseek",
            "reasoning_effort": "low",
        },
    ],
)
def test_pi_annotator_can_override_inherited_worker_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    annotator_config: dict[str, str],
) -> None:
    pi_home = tmp_path / "pi-home"
    pi_home.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi_home))
    monkeypatch.setenv("PI_PROVIDER", "glm-proxy")
    runtime, run_id, [candidate] = _search_with_candidates(
        tmp_path,
        1,
        strategy_updates={
            "worker_host": "pi-rpc",
            "worker_budget": {"max_runtime_seconds": 60},
            "worker_launch": {
                "model": "GLM-5.2",
                "reasoning_effort": "high",
            },
            "evidence_annotator": annotator_config,
        },
    )
    candidate_id, session_id, workspace = candidate
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Set the Pi candidate value",
    )

    task = runtime._load_evidence_annotation_task(run_id, candidate_id, 1)
    assert task is not None and task.profile is not None
    assert task.state == "pending"
    assert task.profile.host == "pi-rpc"
    assert task.profile.model == annotator_config["model"]
    assert task.profile.pi_provider == "deepseek"
    assert task.profile.reasoning_effort == "low"


def test_evidence_commit_captures_change_back_to_source(tmp_path: Path) -> None:
    runtime, run_id, [candidate] = _search_with_candidates(tmp_path, 1)
    candidate_id, session_id, workspace = candidate
    program = workspace / "initial_program.py"

    program.write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Set the candidate value to one",
    )
    settled_head = runtime._load_candidate_record(
        run_id, candidate_id
    ).results_ledger_git_head

    program.write_text("VALUE = 0\n", encoding="utf-8")
    report = runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Restore the source value",
    )

    iteration = runtime._load_candidate_record(run_id, candidate_id).iterations[-1]
    assert report.disposition == "discard"
    assert iteration.attempt_base_git_head == settled_head
    assert iteration.attempt_changed_files == ["initial_program.py"]
    assert _git(workspace, "show", f"{iteration.git_head}:initial_program.py") == (
        "VALUE = 0"
    )
    context = runtime._evidence_annotation_context(run_id, candidate_id, 2)
    assert "-VALUE = 1" in context["actual_diff"]
    assert "+VALUE = 0" in context["actual_diff"]
    assert program.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_evidence_diff_includes_bounded_function_context(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unchanged_body = "".join(
        f"    # unchanged context line {index}\n" for index in range(24)
    )
    (project / "initial_program.py").write_text(
        "def calculate():\n"
        "    initialized_value = 1\n"
        f"{unchanged_body}"
        "    return 0\n",
        encoding="utf-8",
    )
    (project / "evaluator.py").write_text(
        "import json\n"
        "from initial_program import calculate\n"
        "print(json.dumps({'combined_score': float(calculate())}))\n",
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".gp")
    frozen = runtime.freeze_spec(
        spec_for(project, max_parallel=1),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)
    program = task.workspace / "initial_program.py"
    program.write_text(
        "def calculate():\n"
        "    initialized_value = 1\n"
        f"{unchanged_body}"
        "    return 1\n",
        encoding="utf-8",
    )

    runtime.run_verifier(
        run_id,
        task.candidate_id,
        agent_session_id=session.agent_session_id,
        hypothesis="Return the initialized result",
    )
    context = runtime._evidence_annotation_context(run_id, task.candidate_id, 1)

    assert "initialized_value = 1" in context["actual_diff"]
    assert "initialized_value = 1" in context["candidate_diff"]
    assert "function context" in context["diff_context_policy"]
    assert "byte-bounded" in context["diff_context_policy"]


def test_evidence_diff_spans_all_manual_commits_in_attempt(tmp_path: Path) -> None:
    runtime, run_id, [candidate] = _search_with_candidates(tmp_path, 1)
    candidate_id, session_id, workspace = candidate
    program = workspace / "initial_program.py"

    program.write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Establish the first candidate value",
    )
    base = runtime._load_candidate_record(run_id, candidate_id).results_ledger_git_head

    program.write_text("VALUE = 2\n", encoding="utf-8")
    git_commit_all(workspace, "manual intermediate attempt")
    program.write_text("VALUE = 3\n", encoding="utf-8")
    attempt = git_commit_all(workspace, "manual final attempt")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Raise the value through two committed revisions",
    )

    iteration = runtime._load_candidate_record(run_id, candidate_id).iterations[-1]
    context = runtime._evidence_annotation_context(run_id, candidate_id, 2)
    assert iteration.attempt_base_git_head == base
    assert iteration.git_head == attempt
    assert iteration.attempt_changed_files == ["initial_program.py"]
    assert "-VALUE = 1" in context["actual_diff"]
    assert "+VALUE = 3" in context["actual_diff"]
    assert "+VALUE = 2" not in context["actual_diff"]


def test_evidence_diff_omits_binary_payload(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    spec_data = spec_for(project, max_parallel=1).model_dump(mode="json")
    spec_data["workspace"] = {"backend": "git_worktree"}
    spec_data["edit_surface"]["allow"].append("checkpoint.bin")
    runtime = FileSearchRuntime(tmp_path / ".gp")
    frozen = runtime.freeze_spec(
        SearchSpec.model_validate(spec_data),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)

    (task.workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    (task.workspace / "checkpoint.bin").write_bytes(b"\0" + b"x" * 200_000)
    runtime.run_verifier(
        run_id,
        task.candidate_id,
        agent_session_id=session.agent_session_id,
        hypothesis="Add a trained checkpoint",
    )

    context = runtime._evidence_annotation_context(run_id, task.candidate_id, 1)
    assert "checkpoint.bin" in context["actual_diff"]
    assert "Binary files" in context["actual_diff"]
    assert "GIT binary patch" not in context["actual_diff"]
    assert len(context["actual_diff"].encode("utf-8")) < 20_000
