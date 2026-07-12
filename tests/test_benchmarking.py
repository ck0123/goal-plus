from __future__ import annotations

import json
from pathlib import Path

from goal_plus.benchmarking.adapters.search_spec import (
    build_search_spec,
    prepare_case_workspace,
)
from goal_plus.benchmarking.cases import BenchmarkCase, Choice
from goal_plus.benchmarking.datasets import (
    SUPPORTED_BENCHMARKS,
    row_to_case,
)
from goal_plus.benchmarking.reporting import compare_paper_results
from goal_plus.benchmarking.runners.direct import run_direct_case
from goal_plus.benchmarking.runners.search_runtime import run_search_case
from goal_plus.benchmarking.scoring import normalize_choice_label, score_prediction
from goal_plus.runtime import FileSearchRuntime


def test_scores_mcq_and_numeric_answers() -> None:
    mcq = BenchmarkCase(
        benchmark="formal_logic",
        task_id="formal_logic:1",
        question="Which option is valid?",
        choices=[
            Choice(label="A", text="invalid"),
            Choice(label="B", text="valid"),
            Choice(label="C", text="irrelevant"),
            Choice(label="D", text="unknown"),
        ],
        gold="B",
        answer_type="mcq",
    )
    assert score_prediction(mcq, "The answer is (B).").correct is True
    assert score_prediction(mcq, "{final answer: (B)}").correct is True
    assert normalize_choice_label("(B)") == "B"
    assert score_prediction(mcq, "I choose C").score == 0.0

    numeric = BenchmarkCase(
        benchmark="gsm8k",
        task_id="gsm8k:1",
        question="2 + 2?",
        choices=[],
        gold="4",
        answer_type="numeric",
    )
    assert score_prediction(numeric, "#### 4").correct is True
    assert score_prediction(numeric, "{final answer: 4.0}").correct is True
    assert score_prediction(numeric, "The answer is 5").correct is False


def test_row_adapters_cover_initial_benchmarks() -> None:
    assert {
        "formal_logic",
        "arc",
        "winogrande",
        "truthfulqa",
        "gsm8k",
    }.issubset(SUPPORTED_BENCHMARKS)

    formal = row_to_case(
        "formal_logic",
        {
            "question": "Q?",
            "choices": ["a", "b", "c", "d"],
            "answer": 1,
        },
        index=0,
    )
    assert formal.choices[1].label == "B"
    assert formal.gold == "B"

    arc = row_to_case(
        "arc",
        {
            "id": "arc1",
            "question": "Q?",
            "choices": {"label": ["1", "2", "3", "4"], "text": ["a", "b", "c", "d"]},
            "answerKey": "2",
        },
        index=0,
    )
    assert [choice.label for choice in arc.choices] == ["A", "B", "C", "D"]
    assert arc.gold == "B"

    winogrande = row_to_case(
        "winogrande",
        {
            "sentence": "The _ is blue.",
            "option1": "sky",
            "option2": "grass",
            "answer": "1",
        },
        index=0,
    )
    assert winogrande.gold == "A"

    truthfulqa = row_to_case(
        "truthfulqa",
        {
            "question": "Q?",
            "mc1_targets": {"choices": ["true", "false"], "labels": [1, 0]},
        },
        index=0,
    )
    assert truthfulqa.gold == "A"

    gsm8k = row_to_case(
        "gsm8k",
        {"question": "Q?", "answer": "reasoning\n#### 42"},
        index=0,
    )
    assert gsm8k.gold == "42"
    assert gsm8k.answer_type == "numeric"


def test_search_spec_adapter_verifies_case_workspace(tmp_path: Path) -> None:
    case = BenchmarkCase(
        benchmark="formal_logic",
        task_id="formal_logic:demo/1",
        question="Pick the valid option.",
        choices=[
            Choice(label="A", text="bad"),
            Choice(label="B", text="good"),
        ],
        gold="B",
        answer_type="mcq",
    )
    workspace = prepare_case_workspace(case, tmp_path / "workspaces")
    assert "gold" not in (workspace.source_path / "QUESTION.md").read_text(encoding="utf-8").lower()

    spec = build_search_spec(
        workspace,
        max_candidates=1,
        max_parallel=1,
        worker_host="pi-rpc",
        max_runtime_seconds=60,
    )
    assert spec["metric_name"] == "accuracy"
    assert spec["edit_surface"]["allow"] == ["answer.json"]
    assert spec["strategy"]["worker_host"] == "pi-rpc"

    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(
        runtime_spec := __import__(
            "goal_plus.models",
            fromlist=["SearchSpec"],
        ).SearchSpec.model_validate(spec),
        [],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    (Path(task.workspace) / "answer.json").write_text(
        json.dumps({"answer": "B"}),
        encoding="utf-8",
    )

    report = runtime.run_verifier(run_id, task.candidate_id)
    assert runtime_spec.metric_name == "accuracy"
    assert report.aggregate_score == 1.0


def test_paper_compare_aligns_single_case() -> None:
    comparison = compare_paper_results(
        ours=[
            {
                "benchmark": "formal_logic",
                "task_id": "formal_logic:1",
                "prediction": "B",
                "gold": "B",
                "correct": True,
            }
        ],
        paper=[
            {
                "benchmark": "formal_logic",
                "task_id": "formal_logic:1",
                "paper_prediction": "(C)",
                "paper_gold": "(B)",
                "paper_correct": False,
            }
        ],
    )
    assert comparison["n_matched"] == 1
    assert comparison["accuracy_delta"] == 1.0
    assert comparison["cases"][0]["ours_correct"] is True
    assert comparison["cases"][0]["paper_correct"] is False
    assert comparison["cases"][0]["paper_prediction"] == "C"


def test_paper_compare_derives_correctness_from_paper_labels() -> None:
    comparison = compare_paper_results(
        ours=[
            {
                "benchmark": "gsm8k",
                "task_id": "gsm8k:1",
                "prediction": "18",
                "gold": "18",
                "correct": True,
            }
        ],
        paper=[
            {
                "benchmark": "gsm8k",
                "task_id": "gsm8k:1",
                "paper_prediction": "18.0",
                "paper_gold": "18",
            }
        ],
    )
    assert comparison["n_matched"] == 1
    assert comparison["paper_accuracy"] == 1.0


def test_runners_emit_paper_compatible_and_search_diagnostic_rows(tmp_path: Path) -> None:
    case = BenchmarkCase(
        benchmark="formal_logic",
        task_id="formal_logic:runner",
        question="Pick one.",
        choices=[Choice(label="A", text="bad"), Choice(label="B", text="good")],
        gold="B",
        answer_type="mcq",
    )

    direct = run_direct_case(case, prediction_text="B")
    assert direct["mode"] == "direct"
    assert direct["correct"] is True
    assert direct["paper_gold"] == "(B)"
    assert direct["paper_prediction"] == "(B)"
    assert direct["paper_response"] == "{final answer: (B)}"

    search = run_search_case(
        case,
        root_dir=tmp_path / "bench-run",
        worker_backend="fixed",
        fixed_answer="B",
        max_candidates=1,
    )
    assert search["paper_result"]["mode"] == "mcp_search"
    assert search["paper_result"]["correct"] is True
    assert search["paper_result"]["paper_prediction"] == "(B)"
    assert search["search_diagnostics"]["candidate_count"] == 1
    assert search["search_diagnostics"]["pass_at_k"] is True
