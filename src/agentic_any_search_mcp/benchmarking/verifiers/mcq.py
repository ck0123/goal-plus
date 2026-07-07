from __future__ import annotations

from pathlib import Path

from ..cases import BenchmarkCase, Choice
from ..scoring import score_prediction
from .common import emit, parser, read_gold, read_prediction


def main() -> int:
    args = parser().parse_args()
    gold = read_gold(Path(args.gold_file))
    prediction = read_prediction(Path(args.prediction))
    case = BenchmarkCase(
        benchmark=str(gold["benchmark"]),
        task_id=str(gold["task_id"]),
        question="",
        choices=[Choice(**choice) for choice in gold.get("choices", [])],
        gold=str(gold["gold"]),
        answer_type="mcq",
    )
    result = score_prediction(case, prediction)
    return emit(
        {
            "accuracy": result.score,
            "score": result.score,
            "correct": result.correct,
            "prediction": result.prediction,
            "gold": result.gold,
            "parser_error": result.parser_error,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
