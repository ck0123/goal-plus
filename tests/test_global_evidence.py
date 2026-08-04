from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess

import pytest

from goal_plus.models import (
    AcceptanceViewAssessment,
    EvidenceViewRecord,
    SearchSpec,
)
from goal_plus.runtime import FileSearchRuntime
from tests._runtime_helpers import git_commit_all, make_project, spec_for


def _search_with_candidates(
    tmp_path: Path,
    count: int,
    *,
    strategy_updates: dict | None = None,
    acceptance_view: dict | None = None,
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
    if acceptance_view is not None:
        spec_data["acceptance_view"] = acceptance_view
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


def test_global_evidence_is_immediate_and_view_is_late_bound(tmp_path: Path) -> None:
    runtime, run_id, candidates = _search_with_candidates(tmp_path, 2)
    first, second = candidates

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
    assert all(entry["acceptance_view"] is None for entry in view)

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
    assert runtime.get_global_evidence(first[1])[-1]["view"] == (
        "Changed the candidate value from two to one without altering the evaluator."
    )
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


def test_global_evidence_presents_structured_acceptance_view(tmp_path: Path) -> None:
    contract = {
        "rubric_name": "EdgeBench hidden generalization",
        "benchmark_context": "Local and hidden workloads differ.",
        "criteria": [
            {
                "id": "input_generalization",
                "category": "hidden_generalization",
                "description": "Handle valid inputs beyond public examples.",
                "importance": "high",
            }
        ],
    }
    runtime, run_id, [candidate] = _search_with_candidates(
        tmp_path,
        1,
        acceptance_view=contract,
    )
    candidate_id, session_id, workspace = candidate
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    report = runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        hypothesis="Generalize the implementation",
    )
    assert report.disposition == "keep"

    context = runtime._evidence_annotation_context(run_id, candidate_id, 1)
    assert context["acceptance_contract"]["rubric_name"] == contract["rubric_name"]
    assert context["acceptance_contract"]["affects_final_result"] is False

    task = runtime._load_evidence_annotation_task(run_id, candidate_id, 1)
    assert task is not None
    runtime._write_evidence_annotation_task(
        task.model_copy(
            update={
                "state": "completed",
                "view": EvidenceViewRecord(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    iteration=1,
                    attempt_commit=task.attempt_commit,
                    description="Changed the implementation to handle more inputs.",
                    acceptance_view=AcceptanceViewAssessment.model_validate(
                        {
                            "summary": "The diff provides partial public evidence.",
                            "criteria": [
                                {
                                    "criterion_id": "input_generalization",
                                    "status": "partial",
                                    "confidence": "medium",
                                    "evidence": ["initial_program.py diff"],
                                    "rationale": "The broader branch is visible, but hidden behavior is unknown.",
                                }
                            ],
                        }
                    ),
                    created_at="2026-01-01T00:00:00Z",
                ),
            }
        )
    )

    [entry] = runtime.get_global_evidence(session_id)
    assert entry["score"] == 1.0
    assert entry["acceptance_view"]["criteria"][0] == {
        "criterion_id": "input_generalization",
        "status": "partial",
        "confidence": "medium",
        "evidence": ["initial_program.py diff"],
        "rationale": "The broader branch is visible, but hidden behavior is unknown.",
    }


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
