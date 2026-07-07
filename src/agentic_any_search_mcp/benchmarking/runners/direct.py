from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..cases import BenchmarkCase
from ..paper_compat import paper_fields
from ..scoring import score_prediction


ModelFn = Callable[[BenchmarkCase], str]


def run_direct_case(
    case: BenchmarkCase,
    *,
    prediction_text: str | None = None,
    model_fn: ModelFn | None = None,
    mode: str = "direct",
) -> dict[str, Any]:
    if prediction_text is None:
        if model_fn is None:
            raise ValueError("run_direct_case requires prediction_text or model_fn")
        prediction_text = model_fn(case)
    scored = score_prediction(case, prediction_text)
    return {
        "benchmark": case.benchmark,
        "task_id": case.task_id,
        "question": case.question,
        "choices": [choice.__dict__ for choice in case.choices],
        "gold": scored.gold,
        "prediction": scored.prediction,
        "correct": scored.correct,
        "score": scored.score,
        "mode": mode,
        "n_calls": 1 if model_fn is not None else 0,
        "parser_error": scored.parser_error,
        "raw_prediction": prediction_text,
    } | paper_fields(case, scored)
