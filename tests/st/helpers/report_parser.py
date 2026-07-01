from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class StReport:
    scenario: str
    run_id: str | None
    candidates: list[dict]
    selected_candidate_id: str | None
    best_score: float | None
    report_path: str | None
    extra: dict
    raw: str


_FENCE_RE = re.compile(
    r"```(?:st_report|json)\s*\n(?P<body>\{.*?\})\s*\n```",
    re.DOTALL,
)


def extract_st_report(stdout: str) -> StReport | None:
    """Find the LAST fenced JSON block tagged st_report in stdout."""
    matches = list(_FENCE_RE.finditer(stdout))
    if not matches:
        return None
    body = matches[-1].group("body")
    data = json.loads(body)
    return StReport(
        scenario=data.get("scenario", ""),
        run_id=data.get("run_id"),
        candidates=data.get("candidates", []) or [],
        selected_candidate_id=data.get("selected_candidate_id"),
        best_score=data.get("best_score"),
        report_path=data.get("report_path"),
        extra=data.get("extra", {}) or {},
        raw=body,
    )


def find_run_id_in_stdout(stdout: str) -> str | None:
    """Best-effort fallback: look for run_XXXXXX pattern if no JSON report."""
    m = re.search(r"run_[A-Za-z0-9_-]{6,}", stdout)
    return m.group(0) if m else None
