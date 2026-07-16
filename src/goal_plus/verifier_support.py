from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable, Iterator, Mapping

from goal_plus.workspaces import copy_source_tree


VERIFIER_TMPDIR_ENV = "GOAL_PLUS_VERIFIER_TMPDIR"
VERIFIER_DIAGNOSTICS_ENV = "GOAL_PLUS_VERIFIER_DIAGNOSTICS_DIR"


@dataclass(frozen=True)
class IsolatedVerifierWorkspace:
    live_workspace: Path
    workspace: Path
    temp_root: Path
    diagnostics_dir: Path | None


def sanitized_evaluator_environment(
    *,
    python_paths: Iterable[Path | str] = (),
    base_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an evaluator environment without the verifier's live PYTHONPATH.

    Goal Plus adds the verifier workspace to its process ``PYTHONPATH`` so
    verifier-local modules remain importable. A nested evaluator which builds
    and installs a Candidate package must not inherit that path, because the
    live source package can shadow the newly installed wheel.
    """
    env = dict(os.environ if base_environment is None else base_environment)
    explicit_paths = tuple(str(Path(path).resolve()) for path in python_paths)
    if explicit_paths:
        env["PYTHONPATH"] = os.pathsep.join(explicit_paths)
    else:
        env.pop("PYTHONPATH", None)
    return env


@contextmanager
def isolated_verifier_workspace(
    live_workspace: Path | str,
    *,
    required_paths: Iterable[Path | str] = (),
) -> Iterator[IsolatedVerifierWorkspace]:
    """Copy verifier inputs into invocation-local scratch before building.

    Runtime calls provide ``GOAL_PLUS_VERIFIER_TMPDIR``. Direct verifier
    self-tests receive an equivalent managed temporary directory.
    """
    live = Path(live_workspace).resolve()
    if not live.is_dir():
        raise ValueError(f"verifier workspace is not a directory: {live}")

    provided_temp = os.environ.get(VERIFIER_TMPDIR_ENV)
    managed_temp: tempfile.TemporaryDirectory[str] | None = None
    if provided_temp:
        temp_root = Path(provided_temp).resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
    else:
        managed_temp = tempfile.TemporaryDirectory(
            prefix="goal-plus-verifier-local-"
        )
        temp_root = Path(managed_temp.name).resolve()

    try:
        if temp_root == live or live in temp_root.parents:
            raise ValueError(
                "GOAL_PLUS_VERIFIER_TMPDIR must be outside the live candidate workspace"
            )
        workspace = temp_root / "workspace"
        if workspace.is_symlink() or workspace.is_file():
            workspace.unlink()
        elif workspace.exists():
            shutil.rmtree(workspace)
        copy_source_tree(live, workspace)
        missing = [
            relative.as_posix()
            for relative in _required_relative_paths(required_paths)
            if not (workspace / relative).exists()
        ]
        if missing:
            raise ValueError(
                "isolated verifier workspace is missing required source inputs: "
                + ", ".join(missing)
            )

        diagnostics_value = os.environ.get(VERIFIER_DIAGNOSTICS_ENV)
        diagnostics_dir = (
            Path(diagnostics_value).resolve() if diagnostics_value else None
        )
        if diagnostics_dir is not None:
            if diagnostics_dir == live or live in diagnostics_dir.parents:
                raise ValueError(
                    "GOAL_PLUS_VERIFIER_DIAGNOSTICS_DIR must be outside the "
                    "live candidate workspace"
                )
            diagnostics_dir.mkdir(parents=True, exist_ok=True)

        yield IsolatedVerifierWorkspace(
            live_workspace=live,
            workspace=workspace,
            temp_root=temp_root,
            diagnostics_dir=diagnostics_dir,
        )
    finally:
        if managed_temp is not None:
            managed_temp.cleanup()


def _required_relative_paths(
    paths: Iterable[Path | str],
) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for value in paths:
        path = Path(value)
        if path.is_absolute() or path == Path(".") or ".." in path.parts:
            raise ValueError(f"required verifier path must be relative: {value}")
        normalized.append(path)
    return tuple(normalized)


def parse_cannbench_report(
    report_path: Path | str,
    *,
    expected_case_ids: Iterable[str] | None = None,
    expected_operator: str | None = None,
    require_performance: bool = True,
) -> dict[str, object]:
    """Validate one official CANNBench report and return ranking evidence.

    Precision is a hard gate. The returned ``cannbench_score`` is emitted only
    for a complete, finite, performance-enabled report.
    """
    path = Path(report_path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read CANNBench report: {exc}") from exc
    if not isinstance(report, dict):
        raise ValueError("CANNBench report root must be an object")

    operators = report.get("operators")
    if not isinstance(operators, list) or not operators or not all(
        isinstance(operator, dict) for operator in operators
    ):
        raise ValueError("CANNBench report must contain operator results")
    if expected_operator is not None:
        matches = [
            operator
            for operator in operators
            if str(operator.get("operator", "")).casefold()
            == expected_operator.casefold()
        ]
        if len(matches) != 1 or len(operators) != 1:
            names = [str(operator.get("operator", "")) for operator in operators]
            raise ValueError(
                f"CANNBench report operator mismatch: expected {expected_operator}, "
                f"got {names}"
            )

    cases: list[dict[str, object]] = []
    for operator in operators:
        operator_cases = operator.get("cases")
        if not isinstance(operator_cases, list) or not all(
            isinstance(case, dict) for case in operator_cases
        ):
            raise ValueError("CANNBench operator result has invalid cases")
        cases.extend(operator_cases)
    if not cases:
        raise ValueError("CANNBench report contains no cases")

    case_ids = [str(case.get("case_id", "")) for case in cases]
    if any(not case_id for case_id in case_ids) or len(set(case_ids)) != len(case_ids):
        raise ValueError("CANNBench report case IDs are missing or duplicated")
    if expected_case_ids is not None:
        expected = tuple(str(case_id) for case_id in expected_case_ids)
        if not expected or len(set(expected)) != len(expected):
            raise ValueError("expected CANNBench case IDs are empty or duplicated")
        missing = sorted(set(expected) - set(case_ids))
        unexpected = sorted(set(case_ids) - set(expected))
        if missing or unexpected:
            raise ValueError(
                "CANNBench report case set mismatch: "
                f"missing={missing}, unexpected={unexpected}"
            )

    failed_case_ids: list[str] = []
    missing_performance: list[str] = []
    for case_id, case in zip(case_ids, cases):
        accuracy = case.get("accuracy")
        if (
            case.get("status") != "success"
            or not isinstance(accuracy, dict)
            or accuracy.get("passed") is not True
        ):
            failed_case_ids.append(case_id)
        if require_performance and not _finite_positive(case.get("elapsed_us")):
            missing_performance.append(case_id)
    if failed_case_ids:
        raise ValueError(
            f"CANNBench precision gate failed for cases: {failed_case_ids}"
        )
    if missing_performance:
        raise ValueError(
            "CANNBench report has no performance measurement for cases: "
            f"{missing_performance}"
        )

    total_cases = report.get("total_cases")
    passed_cases = report.get("passed_cases")
    failed_cases = report.get("failed_cases")
    if (
        total_cases != len(cases)
        or passed_cases != len(cases)
        or failed_cases != 0
    ):
        raise ValueError(
            "CANNBench report summary does not prove all cases passed: "
            f"total={total_cases}, passed={passed_cases}, failed={failed_cases}, "
            f"observed={len(cases)}"
        )

    score = report.get("overall_score")
    if not _finite_number(score):
        raise ValueError(f"CANNBench overall_score is not finite: {score!r}")

    operator_scores: dict[str, float] = {}
    for operator in operators:
        operator_score = operator.get("score")
        if not _finite_number(operator_score):
            raise ValueError(
                "CANNBench operator score is not finite for "
                f"{operator.get('operator')}: {operator_score!r}"
            )
        operator_scores[str(operator.get("operator", ""))] = float(operator_score)

    return {
        "passed": True,
        "metric_name": "cannbench_score",
        "cannbench_score": float(score),
        "case_ids": case_ids,
        "passed_case_ids": case_ids,
        "failed_case_ids": [],
        "total_cases": len(cases),
        "operator_scores": operator_scores,
        "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _finite_positive(value: object) -> bool:
    return _finite_number(value) and float(value) > 0
