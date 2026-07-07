from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .cases import BenchmarkCase


_BOXED_RE = re.compile(r"\\boxed\{([^{}]+)\}")
_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.\d+|\d+)(?:/\d+)?")


@dataclass(frozen=True)
class ScoreResult:
    prediction: str
    gold: str
    correct: bool
    score: float
    parser_error: str | None = None


def normalize_choice_label(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    bracketed = re.fullmatch(r"\(?\s*([A-Za-z])\s*\)?", stripped)
    if bracketed:
        return bracketed.group(1).upper()
    final_answer = re.search(r"final\s+answer\s*:\s*\(?\s*([A-Za-z])\s*\)?", stripped, flags=re.IGNORECASE)
    if final_answer:
        return final_answer.group(1).upper()
    if stripped.isdigit():
        index = int(stripped)
        if index >= 1:
            return chr(ord("A") + index - 1)
    return stripped.upper()


def parse_mcq_prediction(text: str, allowed_labels: list[str]) -> str:
    allowed = [label.upper() for label in allowed_labels]
    if not allowed:
        return ""
    patterns = [
        r"\(([A-Z])\)",
        r"\banswer\s*(?:is|:)?\s*([A-Z])\b",
        r"\boption\s*([A-Z])\b",
        r"\b([A-Z])\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            label = match.group(1).upper()
            if label in allowed:
                return label
    return ""


def normalize_numeric(value: str) -> str:
    return value.strip().replace(",", "")


def parse_numeric_prediction(text: str) -> str:
    boxed = _BOXED_RE.findall(text)
    if boxed:
        return normalize_numeric(boxed[-1])
    if "####" in text:
        tail = text.rsplit("####", 1)[-1]
        numbers = _NUMBER_RE.findall(tail)
        if numbers:
            return normalize_numeric(numbers[-1])
    numbers = _NUMBER_RE.findall(text)
    return normalize_numeric(numbers[-1]) if numbers else ""


def score_prediction(case: BenchmarkCase, raw_prediction: str) -> ScoreResult:
    if case.answer_type == "mcq":
        labels = [choice.label for choice in case.choices]
        prediction = parse_mcq_prediction(raw_prediction, labels)
        gold = normalize_choice_label(case.gold)
    else:
        prediction = parse_numeric_prediction(raw_prediction)
        gold = normalize_numeric(case.gold)

    if not prediction:
        return ScoreResult(
            prediction="",
            gold=gold,
            correct=False,
            score=0.0,
            parser_error="empty_prediction",
        )
    if case.answer_type == "numeric":
        correct = numeric_equal(prediction, gold)
    else:
        correct = prediction == gold
    return ScoreResult(
        prediction=prediction,
        gold=gold,
        correct=correct,
        score=1.0 if correct else 0.0,
    )


def numeric_equal(left: str, right: str) -> bool:
    try:
        return Decimal(left) == Decimal(right)
    except InvalidOperation:
        return left == right
