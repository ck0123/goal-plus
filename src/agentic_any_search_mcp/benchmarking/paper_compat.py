from __future__ import annotations

from .cases import BenchmarkCase
from .scoring import ScoreResult, normalize_choice_label


def paper_label(case: BenchmarkCase, value: str) -> str:
    if case.answer_type == "mcq":
        label = normalize_choice_label(value)
        return f"({label})" if label else ""
    return value


def paper_response(case: BenchmarkCase, scored: ScoreResult) -> str:
    if case.answer_type == "mcq":
        return f"{{final answer: {paper_label(case, scored.prediction)}}}"
    return f"{{final answer: {scored.prediction}}}"


def paper_fields(case: BenchmarkCase, scored: ScoreResult) -> dict[str, str]:
    return {
        "paper_gold": paper_label(case, scored.gold),
        "paper_prediction": paper_label(case, scored.prediction),
        "paper_response": paper_response(case, scored),
    }
