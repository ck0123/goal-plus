from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from goal_plus.models import SearchSpec
from goal_plus.runtime import FileSearchRuntime
from goal_plus.tools import SearchTools


ROOT = Path(__file__).resolve().parents[1]


EXAMPLE_SPECS = [
    (
        "circle_packing_search_spec.json",
        ["tests/fixtures/circle_packing/evaluator.py"],
        "combined_score",
        {"max_candidates": 4, "max_parallel": 2, "worker_agent_type": "SearchCandidateAgentFlash"},
    ),
    (
        "signal_processing_search_spec.json",
        ["tests/fixtures/signal_processing/evaluator.py"],
        "overall_score",
        {"max_candidates": 8, "max_parallel": 4, "worker_agent_type": "SearchCandidateAgent"},
    ),
    (
        "edgebench_ad_placement_search_spec.json",
        [
            "examples/edgebench-ad-placement/workspace/.goal-plus-verifiers/ad_local_score.py",
            "examples/edgebench-ad-placement/workspace/tools/bin/gen",
            "examples/edgebench-ad-placement/workspace/tools/bin/tester",
        ],
        "local_score_sum",
        {
            "max_candidates": 4,
            "max_parallel": 2,
            "strategy_name": "agent_guided",
            "worker_host": "pi-rpc",
            "worker_agent_type": None,
            "max_runtime_seconds": 240,
            "max_turns": 6,
        },
    ),
]

# EdgeBench's C++ compile/public-score contract has dedicated coverage in
# test_edgebench_ad_placement_example.py. Re-running it across two generic
# rounds compiles the same solution several more times without adding runtime
# state-machine coverage beyond the Python fixtures below.
RUNTIME_EXAMPLE_SPECS = [
    case for case in EXAMPLE_SPECS if case[0] != "edgebench_ad_placement_search_spec.json"
]

def load_expected(spec_name: str) -> dict:
    for name, _, _, expected in EXAMPLE_SPECS:
        if name == spec_name:
            return expected
    raise KeyError(spec_name)


def load_example_spec(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("spec_name", "verifier_paths", "metric_name", "expected"), EXAMPLE_SPECS)
def test_parallel_loop_example_specs_are_valid(
    spec_name: str,
    verifier_paths: list[str],
    metric_name: str,
    expected: dict,
) -> None:
    spec = load_example_spec(spec_name)
    parsed = SearchSpec.model_validate(spec)

    assert parsed.metric_name == metric_name
    assert parsed.budget.max_candidates == expected["max_candidates"]
    assert parsed.budget.max_parallel == expected["max_parallel"]
    assert parsed.root_hypotheses == []
    assert parsed.constraints["suggested_batch_size"] == 4
    assert parsed.strategy.orchestration_mode == "parallel_loops"
    assert parsed.strategy.worker_agent_type == expected["worker_agent_type"]
    if "strategy_name" in expected:
        assert parsed.strategy.name == expected["strategy_name"]
        assert parsed.strategy.worker_host == expected["worker_host"]
        assert parsed.strategy.worker_budget is not None
        assert (
            parsed.strategy.worker_budget.max_runtime_seconds
            == expected["max_runtime_seconds"]
        )
        assert parsed.strategy.worker_budget.max_turns == expected["max_turns"]
    assert not Path(parsed.source_path).is_absolute()
    assert all((ROOT / verifier_path).exists() for verifier_path in verifier_paths)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("spec_name", "verifier_paths", "metric_name", "expected"),
    RUNTIME_EXAMPLE_SPECS,
)
def test_parallel_loop_examples_create_one_batch_and_verify_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec_name: str,
    verifier_paths: list[str],
    metric_name: str,
    expected: dict,
) -> None:
    monkeypatch.chdir(ROOT)
    tools = SearchTools(FileSearchRuntime(tmp_path / ".search"))
    spec = load_example_spec(spec_name)

    frozen = tools.search_freeze_spec(spec, verifier_paths)
    run_id = tools.search_create(frozen["frozen_spec_id"])["run_id"]

    def verify_baseline(candidate_id: str) -> None:
        session = tools.search_start_agent_session(
            run_id,
            candidate_id,
            {"goal": f"verify baseline fixture path for {candidate_id}"},
        )
        tools.search_get_agent_context(session["agent_session_id"])
        report = tools.search_run_verifier(
            run_id,
            candidate_id,
            agent_session_id=session["agent_session_id"],
        )

        assert report["process_passed"] is True
        assert report["aggregate_score"] is not None
        assert report["aggregate_score"] > 0.0
        assert report["verifier_results"][0]["metrics"][metric_name] == report[
            "aggregate_score"
        ]

    first_plan = tools.search_plan_next(run_id, 4)
    first_proposals = None
    if first_plan["requires_agent_proposals"]:
        first_proposals = [
            {"intent": f"independent Pi proposal {index}"}
            for index in range(1, first_plan["planned_k"] + 1)
        ]
    first_round = tools.search_start_batch(
        run_id,
        first_plan["plan_id"],
        proposals=first_proposals,
    )
    verified_candidate_ids: set[str] = set()
    if first_plan["requires_agent_proposals"]:
        verify_baseline("c001")
        verified_candidate_ids.add("c001")

    first_expected = [
        f"c{index:03d}" for index in range(1, expected["max_parallel"] + 1)
    ]

    assert first_plan["planned_k"] == expected["max_parallel"]
    assert [task["candidate_id"] for task in first_round] == first_expected
    if first_plan["requires_agent_proposals"]:
        assert first_round[0]["hypothesis"] == "independent Pi proposal 1"
    else:
        assert first_round[0]["hypothesis"] == "Independent candidate c001"

    for candidate_id in ("c001", "c002"):
        if candidate_id not in verified_candidate_ids:
            verify_baseline(candidate_id)

    history = tools.search_list_history(run_id)
    assert history["total_candidates"] == expected["max_parallel"]
    assert history["returned_candidates"] == min(5, expected["max_parallel"])
    assert history["sort_by"] == "score"
    assert history["candidates"][0]["score"] >= history["candidates"][1]["score"]
    assert metric_name in history["candidates"][0]["key_metrics"]

    created_history = tools.search_list_history(run_id, top_n=2, sort_by="created")
    assert [item["candidate_id"] for item in created_history["candidates"]] == ["c001", "c002"]

    status = tools.search_status(run_id)
    assert status["candidates_total"] == expected["max_parallel"]
    assert status["candidates_evaluated"] == 2


@pytest.mark.integration
def test_git_worktree_workspace_demo_runs_end_to_end(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "examples" / "workspace-backends" / "run_demo.py"),
            "--runtime-root",
            str(tmp_path / "runtime"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.splitlines()[-1])
    assert summary["workspace_backend"] == "git_worktree"
    assert summary["candidate_ids"] == ["c001", "c002", "c003"]
    assert summary["scores"] == {"c001": 1.0, "c002": 2.0, "c003": 3.0}
    assert summary["shared_git_common_dir"] is True
    assert len(set(summary["branches"].values())) == 3
    assert summary["selected_candidate_id"] == "c003"
    assert Path(summary["report_path"]).exists()


def test_model_opt_gpu_wip_docs_define_future_v100_goal_plus_contract() -> None:
    example_dir = ROOT / "examples" / "model-opt-gpu"
    readme = (example_dir / "README.md").read_text(encoding="utf-8")
    prompt = (
        example_dir / "bert_pytorch_v100_goal_plus.md"
    ).read_text(encoding="utf-8")
    examples_index = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")

    assert "WIP" in readme
    assert "not recommended" in readme.lower()
    assert "not validated" in readme.lower()
    assert "model-opt-gpu" in examples_index
    assert "WIP" in examples_index

    required_prompt_terms = (
        "/goal-plus",
        "pytorch/benchmark",
        "BERT_pytorch",
        "BenchmarkModel",
        "standalone",
        "CUDA_VISIBLE_DEVICES",
        "detected_gpu_count",
        "git_worktree",
        "10%",
        "torch.cuda.synchronize",
        "median",
        "correctness",
        "non-finite",
        "triton_ops/",
        "AI-Infra-Auto-Driven-SKILLS",
    )
    for term in required_prompt_terms:
        assert term in prompt

    assert "max_parallel=1" not in prompt
    assert '"max_parallel": 1' not in prompt
    assert "Do not invent a baseline" in prompt
    assert "Do not start Search Mode" in prompt
