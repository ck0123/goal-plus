from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from goal_plus.models import RunState
from goal_plus.runtime import FileSearchRuntime
from goal_plus.tools import SearchTools


FIXTURE = Path(__file__).parent / "fixtures" / "k_module_problem"


def write_config(path: Path, *, loader: str, preprocess: str, algorithm: str, formatter: str) -> None:
    path.write_text(
        "\n".join(
            [
                "def configure_pipeline():",
                "    return {",
                f'        "loader": "{loader}",',
                f'        "preprocess": "{preprocess}",',
                f'        "algorithm": "{algorithm}",',
                f'        "formatter": "{formatter}",',
                "    }",
                "",
            ]
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    project = tmp_path / "k_module_project"
    shutil.copytree(FIXTURE, project)
    return project


@pytest.fixture()
def tools(tmp_path: Path) -> SearchTools:
    return SearchTools(FileSearchRuntime(tmp_path / ".search"))


def spec_for(project: Path) -> dict:
    return {
        "objective": "maximize k-module pipeline configuration score",
        "metric_name": "combined_score",
        "metric_direction": "maximize",
        "source_path": str(project),
        "edit_surface": {
            "allow": ["initial_program.py"],
            "deny": ["evaluator.py", "config.yaml"],
        },
        "process_verifiers": [
            {
                "name": "k_module_score",
                "role": "ranking_signal",
                "command": [
                    "python",
                    "-c",
                    (
                        "from evaluator import evaluate; "
                        "import json; "
                        "print(json.dumps(evaluate('initial_program.py'), sort_keys=True))"
                    ),
                ],
                "timeout_seconds": 30,
            }
        ],
        "promotion_verifiers": [
            {
                "name": "evaluator_hash_check",
                "role": "anti_cheat_gate",
                "command": ["goal-plus-internal", "check-frozen-hashes"],
            }
        ],
        "budget": {
            "max_candidates": 5,
            "max_parallel": 5,
        },
        "root_hypotheses": [
            "baseline",
            "fix loader",
            "fix loader and preprocess",
            "full target",
            "cheat by touching evaluator",
        ],
        "strategy": {"name": "independent_branches"},
    }


def create_run(tools: SearchTools, project: Path) -> tuple[str, list[dict]]:
    frozen = tools.search_freeze_spec(spec_for(project), [str(project / "evaluator.py")])
    created = tools.search_create(frozen["frozen_spec_id"])
    run_id = created["run_id"]
    plan = tools.search_plan_next(run_id, 5)
    tasks = tools.search_start_batch(run_id, plan["plan_id"])
    assert len(tasks) == 5
    return run_id, tasks


def create_two_round_host_run(
    tools: SearchTools,
    project: Path,
    worker_host: str,
) -> str:
    spec = spec_for(project)
    spec["budget"] = {"max_candidates": 2, "max_parallel": 1}
    spec["strategy"] = {
        "name": "random",
        "worker_mode": "agent-session-pool",
        "worker_host": worker_host,
    }
    frozen = tools.search_freeze_spec(spec, [str(project / "evaluator.py")])
    run_id = tools.search_create(frozen["frozen_spec_id"])["run_id"]

    for round_index in (1, 2):
        plan = tools.search_plan_next(run_id, 1)
        task = tools.search_start_batch(run_id, plan["plan_id"])[0]
        session = tools.search_start_agent_session(
            run_id,
            task["candidate_id"],
            {"goal": f"round {round_index} k-module candidate"},
        )
        if worker_host == "opencode":
            tools.search_bind_opencode_session(
                session["agent_session_id"],
                f"opencode_session_{round_index}",
            )
        else:
            tools.search_bind_agent_handle(
                session["agent_session_id"],
                {
                    "host": worker_host,
                    "external_id": f"{worker_host}_agent_{round_index}",
                    "task_name": f"{worker_host}_task_{round_index}",
                },
            )

        if round_index == 1:
            write_config(
                Path(task["workspace"]) / "initial_program.py",
                loader="csv_reader",
                preprocess="dedupe",
                algorithm="mergesort",
                formatter="xml",
            )
        else:
            write_config(
                Path(task["workspace"]) / "initial_program.py",
                loader="csv_reader",
                preprocess="normalize",
                algorithm="quicksort",
                formatter="json",
            )
        tools.search_run_verifier(
            run_id,
            task["candidate_id"],
            agent_session_id=session["agent_session_id"],
        )
        tools.search_run_verifier(run_id, task["candidate_id"])

    return run_id


def submit(tools: SearchTools, run_id: str, candidate_id: str) -> None:
    tools.search_start_agent_session(run_id, candidate_id, {"goal": f"{candidate_id} ready"})


def test_k_module_end_to_end_selects_best_without_changing_main_workspace(
    tools: SearchTools,
    project_dir: Path,
) -> None:
    run_id, tasks = create_run(tools, project_dir)

    # c001 remains baseline: 0/4.
    submit(tools, run_id, "c001")

    # c002: 1/4.
    write_config(
        Path(tasks[1]["workspace"]) / "initial_program.py",
        loader="csv_reader",
        preprocess="dedupe",
        algorithm="mergesort",
        formatter="xml",
    )
    submit(tools, run_id, "c002")

    # c003: 2/4.
    write_config(
        Path(tasks[2]["workspace"]) / "initial_program.py",
        loader="csv_reader",
        preprocess="normalize",
        algorithm="mergesort",
        formatter="xml",
    )
    submit(tools, run_id, "c003")

    # c004: 4/4.
    write_config(
        Path(tasks[3]["workspace"]) / "initial_program.py",
        loader="csv_reader",
        preprocess="normalize",
        algorithm="quicksort",
        formatter="json",
    )
    submit(tools, run_id, "c004")

    reports = {
        candidate_id: tools.search_run_verifier(run_id, candidate_id)
        for candidate_id in ("c001", "c002", "c003", "c004")
    }

    assert reports["c001"]["aggregate_score"] == 0.0
    assert reports["c002"]["aggregate_score"] == 0.25
    assert reports["c003"]["aggregate_score"] == 0.5
    assert reports["c004"]["aggregate_score"] == 1.0
    assert all(reports[cid]["process_passed"] for cid in reports)

    selection = tools.search_select(run_id)
    assert selection["selected_candidate_id"] == "c004"
    assert selection["selected_score"] == 1.0

    report = tools.search_report(run_id)
    report_path = Path(report["report_path"])
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert "c004" in report_text
    assert "1.0" in report_text
    assert "## Strategy Plans" in report_text
    assert "correct_modules=4" in report_text

    promoted = tools.search_promote(run_id, "c004")
    patch_path = Path(promoted["artifact_path"])
    assert patch_path.exists()
    patch_text = patch_path.read_text(encoding="utf-8")
    assert '"formatter": "json"' in patch_text

    main_program = (project_dir / "initial_program.py").read_text(encoding="utf-8")
    assert '"formatter": "xml"' in main_program

    status = tools.search_status(run_id)
    assert status["state"] == RunState.PROMOTED


@pytest.mark.parametrize("worker_host", ["opencode", "codex", "claude-code"])
def test_k_module_two_rounds_record_host_sessions_and_redispatch(
    tools: SearchTools,
    project_dir: Path,
    worker_host: str,
) -> None:
    run_id = create_two_round_host_run(tools, project_dir, worker_host)

    history = tools.search_list_history(run_id, top_n=5, sort_by="created")
    assert len(history["candidates"]) == 2
    assert [candidate["status"] for candidate in history["candidates"]] == [
        "evaluated",
        "evaluated",
    ]

    agent_sessions = [
        session
        for candidate in history["candidates"]
        for session in candidate["agent_sessions"]
    ]
    assert len(agent_sessions) == 2
    assert {session["host"] for session in agent_sessions} == {worker_host}
    assert all(session["verifier_runs"] == 1 for session in agent_sessions)

    selection = tools.search_select(run_id)
    assert selection["selected_score"] == 1.0
    assert selection["selected_candidate_id"] == history["candidates"][1]["candidate_id"]


def test_denied_verifier_change_is_flagged(
    tools: SearchTools,
    project_dir: Path,
) -> None:
    run_id, tasks = create_run(tools, project_dir)
    cheat_workspace = Path(tasks[4]["workspace"])
    (cheat_workspace / "evaluator.py").write_text(
        "def evaluate(program_path):\n"
        "    return {'combined_score': 1.0, 'valid': True, 'cheated': True}\n",
        encoding="utf-8",
    )

    submit(tools, run_id, "c005")
    report = tools.search_run_verifier(run_id, "c005")

    assert report["process_passed"] is False
    assert report["aggregate_score"] == 0.0
    assert report["touched_denied_files"] is True
    assert report["hardcoding_suspected"] is True
    assert report["verifier_results"][0]["failure_class"] == "EditSurfaceViolation"


def test_frozen_verifier_hash_catches_source_mutation_after_freeze(
    tools: SearchTools,
    project_dir: Path,
) -> None:
    frozen = tools.search_freeze_spec(spec_for(project_dir), [str(project_dir / "evaluator.py")])
    (project_dir / "evaluator.py").write_text(
        "def evaluate(program_path):\n"
        "    return {'combined_score': 1.0, 'valid': True, 'mutated': True}\n",
        encoding="utf-8",
    )

    run_id = tools.search_create(frozen["frozen_spec_id"])["run_id"]
    plan = tools.search_plan_next(run_id, 1)
    task = tools.search_start_batch(run_id, plan["plan_id"])[0]
    submit(tools, run_id, task["candidate_id"])
    report = tools.search_run_verifier(run_id, task["candidate_id"])

    assert report["process_passed"] is False
    assert report["hardcoding_suspected"] is True
    failure_classes = {result["failure_class"] for result in report["verifier_results"]}
    assert "FrozenVerifierModified" in failure_classes
