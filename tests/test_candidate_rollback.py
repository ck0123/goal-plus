from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from goal_plus.models import SearchSpec
from goal_plus.runtime import ACCEPTANCE_VIEW_ENABLED_ENV, FileSearchRuntime
from tests._runtime_helpers import make_project, spec_for


def _candidate(
    tmp_path: Path,
    *,
    direction: str = "maximize",
    backend: str = "copy",
    acceptance_view: bool = False,
) -> tuple[FileSearchRuntime, str, str, Path]:
    project = make_project(tmp_path)
    spec_data = spec_for(project, direction=direction).model_dump(mode="json")
    spec_data["workspace"] = {"backend": backend}
    if acceptance_view:
        spec_data["acceptance_view"] = {
            "rubric_name": "proxy metric generalization",
            "benchmark_context": "The hard process metric is sparse.",
            "criteria": [
                {
                    "id": "generalization",
                    "category": "hidden_generalization",
                    "description": "Avoid specializing only to the visible examples.",
                    "importance": "high",
                }
            ],
        }
    runtime = FileSearchRuntime(tmp_path / ".gp")
    frozen = runtime.freeze_spec(
        SearchSpec.model_validate(spec_data),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    return runtime, run_id, task.candidate_id, task.workspace


def _git(workspace: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=workspace, text=True).strip()


@pytest.mark.parametrize(
    ("direction", "scores"),
    [
        ("maximize", {"seed": 1.0, "improved": 3.0, "worse": 2.0, "equal": 3.0}),
        ("minimize", {"seed": 3.0, "improved": 1.0, "worse": 2.0, "equal": 1.0}),
    ],
)
def test_process_verifier_settles_workspace_to_candidate_best(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    direction: str,
    scores: dict[str, float],
) -> None:
    runtime, run_id, candidate_id, workspace = _candidate(
        tmp_path,
        direction=direction,
        backend="git_worktree",
    )
    program = workspace / "initial_program.py"

    def fake_verify(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        value = program.read_text(encoding="utf-8").split("'", 2)[1]
        if value == "broken":
            return subprocess.CompletedProcess(command, 1, "", "invalid candidate")
        return subprocess.CompletedProcess(
            command,
            0,
            f'{{"combined_score": {scores[value]}}}\n',
            "",
        )

    monkeypatch.setattr(runtime, "_execute_verifier_process", fake_verify)

    reports = []
    expected_best = {
        "seed": "seed",
        "improved": "improved",
        "worse": "improved",
        "equal": "improved",
        "broken": "improved",
    }
    for value in ("seed", "improved", "worse", "equal", "broken"):
        program.write_text(f"VALUE = {value!r}\n", encoding="utf-8")
        reports.append(
            runtime.run_verifier(
                run_id,
                candidate_id,
                hypothesis=f"try {value}",
            )
        )
        assert program.read_text(encoding="utf-8") == (
            f"VALUE = {expected_best[value]!r}\n"
        )

    assert [report.disposition for report in reports] == [
        "keep",
        "keep",
        "discard",
        "discard",
        "failure",
    ]
    assert [report.best_iteration for report in reports] == [1, 2, 2, 2, 2]

    record = runtime._load_candidate_record(run_id, candidate_id)
    assert [iteration.disposition for iteration in record.iterations] == [
        "keep",
        "keep",
        "discard",
        "discard",
        "failure",
    ]
    assert all(iteration.git_head for iteration in record.iterations)
    assert record.iterations[-1].process_passed is False
    history = runtime.list_history(run_id, top_n=1)["candidates"][0]
    assert history["best_iteration"] == 2
    assert history["latest_disposition"] == "failure"
    assert (
        history["workspace_git_head_after_settlement"] == record.results_ledger_git_head
    )

    final_head = _git(workspace, "rev-parse", "HEAD")
    for iteration, value in zip(
        record.iterations,
        ("seed", "improved", "worse", "equal", "broken"),
        strict=True,
    ):
        assert _git(workspace, "show", f"{iteration.git_head}:initial_program.py") == (
            f"VALUE = {value!r}"
        )
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", iteration.git_head, final_head],
            cwd=workspace,
            check=True,
        )

    assert (
        len((workspace / "results.tsv").read_text(encoding="utf-8").splitlines()) == 6
    )
    assert (
        _git(
            workspace,
            "status",
            "--porcelain=v1",
            "--",
            "initial_program.py",
            "results.tsv",
        )
        == ""
    )
    messages = _git(workspace, "log", "--format=%s").splitlines()
    assert sum(message.startswith("goal-plus restore") for message in messages) == 3


def test_first_failed_iteration_restores_pre_attempt_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run_id, candidate_id, workspace = _candidate(tmp_path)
    program = workspace / "initial_program.py"

    monkeypatch.setattr(
        runtime,
        "_execute_verifier_process",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "invalid candidate"
        ),
    )
    program.write_text("VALUE = 'broken'\n", encoding="utf-8")

    report = runtime.run_verifier(run_id, candidate_id, hypothesis="break baseline")

    assert report.disposition == "failure"
    assert report.best_iteration is None
    assert program.read_text(encoding="utf-8") == "VALUE = 0\n"
    iteration = runtime._load_candidate_record(run_id, candidate_id).iterations[0]
    assert _git(workspace, "show", f"{iteration.git_head}:initial_program.py") == (
        "VALUE = 'broken'"
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", iteration.git_head, "HEAD"],
        cwd=workspace,
        check=True,
    )


def test_acceptance_view_retains_latest_valid_hard_score_tie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run_id, candidate_id, workspace = _candidate(
        tmp_path,
        backend="git_worktree",
        acceptance_view=True,
    )
    program = workspace / "initial_program.py"

    def fake_verify(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        value = program.read_text(encoding="utf-8").split("'", 2)[1]
        score = 1.0 if value in {"first", "broader"} else 0.0
        return subprocess.CompletedProcess(
            command,
            0,
            f'{{"combined_score": {score}}}\n',
            "",
        )

    monkeypatch.setattr(runtime, "_execute_verifier_process", fake_verify)
    reports = []
    for value in ("first", "broader", "worse"):
        program.write_text(f"VALUE = {value!r}\n", encoding="utf-8")
        reports.append(
            runtime.run_verifier(
                run_id,
                candidate_id,
                hypothesis=f"try {value}",
            )
        )

    assert [report.disposition for report in reports] == [
        "keep",
        "retain",
        "discard",
    ]
    assert [report.best_iteration for report in reports] == [1, 2, 2]
    assert program.read_text(encoding="utf-8") == "VALUE = 'broader'\n"

    selected = runtime.select(run_id)
    assert selected["selected_iteration"] == 2
    assert selected["selected_score"] == 1.0
    assert program.read_text(encoding="utf-8") == "VALUE = 'broader'\n"


def test_acceptance_view_ablation_restores_strict_hard_score_ties(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ACCEPTANCE_VIEW_ENABLED_ENV, "false")
    runtime, run_id, candidate_id, workspace = _candidate(
        tmp_path,
        backend="git_worktree",
        acceptance_view=True,
    )
    program = workspace / "initial_program.py"

    def fake_verify(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0, '{"combined_score": 1.0}\n', ""
        )

    monkeypatch.setattr(runtime, "_execute_verifier_process", fake_verify)
    dispositions = []
    for value in ("first", "equal"):
        program.write_text(f"VALUE = {value!r}\n", encoding="utf-8")
        dispositions.append(
            runtime.run_verifier(
                run_id, candidate_id, hypothesis=f"try {value}"
            ).disposition
        )

    assert dispositions == ["keep", "discard"]
    assert program.read_text(encoding="utf-8") == "VALUE = 'first'\n"

    selected = runtime.select(run_id)
    assert selected["selected_iteration"] == 1
    assert selected["selected_score"] == 1.0


def test_select_and_promote_keep_all_iteration_commits_reachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run_id, candidate_id, workspace = _candidate(tmp_path)
    program = workspace / "initial_program.py"

    def fake_verify(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        value = program.read_text(encoding="utf-8").split("'", 2)[1]
        return subprocess.CompletedProcess(
            command,
            0,
            f'{{"combined_score": {1 if value == "seed" else 2}}}\n',
            "",
        )

    monkeypatch.setattr(runtime, "_execute_verifier_process", fake_verify)
    for value in ("seed", "best", "equal"):
        program.write_text(f"VALUE = {value!r}\n", encoding="utf-8")
        runtime.run_verifier(run_id, candidate_id, hypothesis=f"try {value}")

    runtime.select(run_id)
    runtime.promote(run_id, candidate_id)

    record = runtime._load_candidate_record(run_id, candidate_id)
    revisions = {
        revision
        for iteration in record.iterations
        for revision in (iteration.git_head, iteration.ledger_git_head)
        if revision is not None
    }
    for revision in revisions:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, "HEAD"],
            cwd=workspace,
            check=True,
        )
    unreachable = _git(workspace, "fsck", "--no-reflogs", "--unreachable")
    assert not any(
        line.startswith("unreachable commit ") for line in unreachable.splitlines()
    )
