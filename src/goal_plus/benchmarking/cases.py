from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AnswerType = Literal["mcq", "numeric"]


@dataclass(frozen=True)
class Choice:
    label: str
    text: str


@dataclass(frozen=True)
class BenchmarkCase:
    benchmark: str
    task_id: str
    question: str
    choices: list[Choice]
    gold: str
    answer_type: AnswerType
    metadata: dict[str, Any] = field(default_factory=dict)


def case_to_dict(case: BenchmarkCase) -> dict[str, Any]:
    return {
        "benchmark": case.benchmark,
        "task_id": case.task_id,
        "question": case.question,
        "choices": [choice.__dict__ for choice in case.choices],
        "gold": case.gold,
        "answer_type": case.answer_type,
        "metadata": case.metadata,
    }


def case_from_dict(data: dict[str, Any]) -> BenchmarkCase:
    return BenchmarkCase(
        benchmark=str(data["benchmark"]),
        task_id=str(data["task_id"]),
        question=str(data["question"]),
        choices=[Choice(label=str(choice["label"]), text=str(choice["text"])) for choice in data.get("choices", [])],
        gold=str(data["gold"]),
        answer_type=data["answer_type"],
        metadata=dict(data.get("metadata") or {}),
    )
