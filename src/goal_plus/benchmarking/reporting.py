from __future__ import annotations

import re
from typing import Any

from .scoring import normalize_numeric, numeric_equal


def compare_paper_results(
    *,
    ours: list[dict[str, Any]],
    paper: list[dict[str, Any]],
) -> dict[str, Any]:
    paper_by_key = {
        (str(row["benchmark"]), str(row["task_id"])): row
        for row in paper
    }
    cases: list[dict[str, Any]] = []
    for ours_row in ours:
        key = (str(ours_row["benchmark"]), str(ours_row["task_id"]))
        paper_row = paper_by_key.get(key)
        if paper_row is None:
            continue
        ours_prediction = _row_prediction(ours_row)
        paper_prediction = _row_prediction(paper_row)
        gold = _row_gold(ours_row) or _row_gold(paper_row)
        cases.append(
            {
                "benchmark": key[0],
                "task_id": key[1],
                "ours_prediction": ours_prediction,
                "paper_prediction": paper_prediction,
                "gold": gold,
                "ours_correct": _row_correct(ours_row, ours_prediction, gold),
                "paper_correct": _row_correct(paper_row, paper_prediction, gold),
            }
        )
    ours_acc = _accuracy([row["ours_correct"] for row in cases])
    paper_acc = _accuracy([row["paper_correct"] for row in cases])
    return {
        "n_matched": len(cases),
        "ours_accuracy": ours_acc,
        "paper_accuracy": paper_acc,
        "accuracy_delta": ours_acc - paper_acc,
        "cases": cases,
    }


def _accuracy(values: list[bool]) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if value) / len(values)


def _row_prediction(row: dict[str, Any]) -> str:
    return _canonical_value(_first_present(row, ["prediction", "paper_prediction", "debate_answer"]))


def _row_gold(row: dict[str, Any]) -> str:
    return _canonical_value(_first_present(row, ["gold", "paper_gold", "answer"]))


def _row_correct(row: dict[str, Any], prediction: str, gold: str) -> bool:
    explicit = _first_present(row, ["correct", "paper_correct"])
    if explicit is not None:
        return bool(explicit)
    if not prediction or not gold:
        return False
    return prediction == gold or numeric_equal(prediction, gold)


def _first_present(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _canonical_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    final_answer = re.search(r"final\s+answer\s*:\s*\(?\s*([A-Za-z])\s*\)?", text, flags=re.IGNORECASE)
    if final_answer:
        return final_answer.group(1).upper()
    bracketed_choice = re.fullmatch(r"\(?\s*([A-Za-z])\s*\)?", text)
    if bracketed_choice:
        return bracketed_choice.group(1).upper()
    return normalize_numeric(text)
