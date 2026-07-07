from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from .cases import BenchmarkCase, Choice
from .scoring import normalize_choice_label, parse_numeric_prediction


SUPPORTED_BENCHMARKS = {
    "formal_logic",
    "arc",
    "winogrande",
    "truthfulqa",
    "gsm8k",
}


HF_SPECS: dict[str, dict[str, str | None]] = {
    "formal_logic": {"path": "cais/mmlu", "name": "formal_logic", "split": "test"},
    "arc": {"path": "allenai/ai2_arc", "name": "ARC-Challenge", "split": "test"},
    "winogrande": {"path": "allenai/winogrande", "name": "winogrande_xl", "split": "validation"},
    "truthfulqa": {"path": "truthfulqa/truthful_qa", "name": "multiple_choice", "split": "validation"},
    "gsm8k": {"path": "openai/gsm8k", "name": "main", "split": "test"},
}


def load_benchmark_cases(
    benchmark: str,
    *,
    split: str | None = None,
    limit: int | None = None,
    task_ids: set[str] | None = None,
    loader: Callable[..., Iterable[dict[str, Any]]] | None = None,
) -> list[BenchmarkCase]:
    key = benchmark.lower()
    if key not in SUPPORTED_BENCHMARKS:
        raise KeyError(f"unsupported benchmark: {benchmark}")
    spec = HF_SPECS[key]
    dataset_loader = loader or _datasets_loader()
    kwargs: dict[str, Any] = {}
    if spec["name"] is not None:
        kwargs["name"] = spec["name"]
    dataset = dataset_loader(spec["path"], split=split or spec["split"], **kwargs)

    cases: list[BenchmarkCase] = []
    for index, row in enumerate(dataset):
        case = row_to_case(key, row, index=index)
        if task_ids is not None and case.task_id not in task_ids:
            continue
        cases.append(case)
        if limit is not None and len(cases) >= limit:
            break
    return cases


def _datasets_loader() -> Callable[..., Iterable[dict[str, Any]]]:
    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover - exercised without optional dep.
        raise RuntimeError(
            "benchmark data loading requires the optional 'datasets' dependency; "
            "install with `pip install -e .[bench]`."
        ) from exc
    return load_dataset


def row_to_case(benchmark: str, row: dict[str, Any], *, index: int) -> BenchmarkCase:
    key = benchmark.lower()
    if key == "formal_logic":
        return _mmlu_case(key, row, index=index)
    if key == "arc":
        return _arc_case(row, index=index)
    if key == "winogrande":
        return _winogrande_case(row, index=index)
    if key == "truthfulqa":
        return _truthfulqa_case(row, index=index)
    if key == "gsm8k":
        return _gsm8k_case(row, index=index)
    raise KeyError(f"unsupported benchmark: {benchmark}")


def _labels(count: int) -> list[str]:
    return [chr(ord("A") + index) for index in range(count)]


def _mmlu_case(benchmark: str, row: dict[str, Any], *, index: int) -> BenchmarkCase:
    choices = [
        Choice(label=label, text=str(text))
        for label, text in zip(_labels(len(row["choices"])), row["choices"])
    ]
    answer = row.get("answer")
    gold = _labels(len(choices))[int(answer)] if isinstance(answer, int) else normalize_choice_label(str(answer))
    return BenchmarkCase(
        benchmark=benchmark,
        task_id=str(row.get("id") or f"{benchmark}:{index}"),
        question=str(row.get("question", "")),
        choices=choices,
        gold=gold,
        answer_type="mcq",
        metadata={"source": "cais/mmlu"},
    )


def _arc_case(row: dict[str, Any], *, index: int) -> BenchmarkCase:
    raw_choices = row.get("choices") or {}
    raw_labels = list(raw_choices.get("label") or [])
    raw_texts = list(raw_choices.get("text") or [])
    label_map = {str(source): target for source, target in zip(raw_labels, _labels(len(raw_texts)))}
    choices = [
        Choice(label=target, text=str(text))
        for target, text in zip(_labels(len(raw_texts)), raw_texts)
    ]
    answer = str(row.get("answerKey", ""))
    return BenchmarkCase(
        benchmark="arc",
        task_id=str(row.get("id") or f"arc:{index}"),
        question=str(row.get("question", "")),
        choices=choices,
        gold=label_map.get(answer, normalize_choice_label(answer)),
        answer_type="mcq",
        metadata={"source": "allenai/ai2_arc", "original_answer": answer},
    )


def _winogrande_case(row: dict[str, Any], *, index: int) -> BenchmarkCase:
    sentence = str(row.get("sentence", ""))
    question = sentence.replace("_", "_____")
    choices = [
        Choice(label="A", text=str(row.get("option1", ""))),
        Choice(label="B", text=str(row.get("option2", ""))),
    ]
    return BenchmarkCase(
        benchmark="winogrande",
        task_id=str(row.get("qID") or row.get("id") or f"winogrande:{index}"),
        question=question,
        choices=choices,
        gold=normalize_choice_label(str(row.get("answer", ""))),
        answer_type="mcq",
        metadata={"source": "allenai/winogrande", "sentence": sentence},
    )


def _truthfulqa_case(row: dict[str, Any], *, index: int) -> BenchmarkCase:
    targets = row.get("mc1_targets") or row.get("mc2_targets") or {}
    raw_choices = list(targets.get("choices") or [])
    raw_labels = list(targets.get("labels") or [])
    choices = [
        Choice(label=label, text=str(text))
        for label, text in zip(_labels(len(raw_choices)), raw_choices)
    ]
    correct_index = next((i for i, label in enumerate(raw_labels) if int(label) == 1), 0)
    return BenchmarkCase(
        benchmark="truthfulqa",
        task_id=str(row.get("id") or f"truthfulqa:{index}"),
        question=str(row.get("question", "")),
        choices=choices,
        gold=_labels(len(choices))[correct_index],
        answer_type="mcq",
        metadata={"source": "truthfulqa/truthful_qa"},
    )


def _gsm8k_case(row: dict[str, Any], *, index: int) -> BenchmarkCase:
    raw_answer = str(row.get("answer", ""))
    gold = parse_numeric_prediction(raw_answer)
    if not gold:
        numbers = re.findall(r"[-+]?(?:\d+\.\d+|\d+)", raw_answer)
        gold = numbers[-1] if numbers else ""
    return BenchmarkCase(
        benchmark="gsm8k",
        task_id=str(row.get("id") or f"gsm8k:{index}"),
        question=str(row.get("question", "")),
        choices=[],
        gold=gold,
        answer_type="numeric",
        metadata={"source": "openai/gsm8k"},
    )
