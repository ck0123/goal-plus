#!/usr/bin/env python3
"""Run CANNBench for a candidate workspace and print goal-plus metrics.

The search runtime parses the last JSON object printed on stdout. This wrapper
keeps all CANNBench-specific logic outside the generic MCP runtime.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _resolve_path(path: str, *, base: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _report_score(payload: dict[str, Any]) -> float | None:
    score = _numeric(payload.get("overall_score"))
    if score is not None:
        return score
    summary = payload.get("summary")
    if isinstance(summary, dict):
        return _numeric(summary.get("overall_score"))
    return None


def _select_report_json(reports_dir: Path) -> Path | None:
    candidates = sorted(
        reports_dir.rglob("*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    fallback = candidates[0] if candidates else None
    for path in candidates:
        try:
            payload = _load_json(path)
        except Exception:
            continue
        if _report_score(payload) is not None:
            return path
    return fallback


def _operator_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    operators = payload.get("operators")
    if not isinstance(operators, list) or not operators:
        return {}
    first = operators[0]
    if not isinstance(first, dict):
        return {}
    return {
        key: first.get(key)
        for key in (
            "operator",
            "rel_path",
            "score",
            "total_score",
            "compile_passed",
            "pass_rate",
            "passed_cases",
            "total_cases",
        )
        if key in first
    }


def summarize_report(report_path: Path, *, elapsed_seconds: float, cannbench_returncode: int) -> dict[str, Any]:
    payload = _load_json(report_path)
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    score = _report_score(payload)
    if score is None:
        score = 0.0

    metrics: dict[str, Any] = {
        "overall_score": score,
        "valid": True,
        "report_json": str(report_path),
        "cannbench_returncode": cannbench_returncode,
        "elapsed_seconds": elapsed_seconds,
    }
    for key in ("total_operators", "total_cases", "passed_cases", "failed_cases"):
        if key in payload:
            metrics[key] = payload[key]
    for key in ("pass_rate", "genuine_pass_rate"):
        if key in summary:
            metrics[key] = summary[key]
    op_metrics = _operator_metrics(payload)
    if op_metrics:
        metrics["operator_report"] = op_metrics
    return metrics


def build_command(args: argparse.Namespace, *, source_dir: Path, reports_dir: Path) -> list[str]:
    script = Path(args.cann_bench_root) / "scripts" / "run_evaluation.sh"
    command = [
        "bash",
        str(script),
        "--bench-name",
        args.bench_name,
        "--source-dir",
        str(source_dir),
        "--task-dir",
        args.task_dir,
        "--operator",
        args.operator,
        "--reports-dir",
        str(reports_dir),
    ]
    if args.device_id is not None:
        command.extend(["--device-id", str(args.device_id)])
    if args.no_perf:
        command.append("--no-perf")
    for item in args.extra_arg:
        command.append(item)
    return command


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cann-bench-root", required=True)
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--source-dir", default=".")
    parser.add_argument("--reports-dir", default="_cannbench_reports")
    parser.add_argument("--bench-name", default="cann")
    parser.add_argument("--device-id")
    parser.add_argument("--no-perf", action="store_true")
    parser.add_argument(
        "--extra-arg",
        action="append",
        default=[],
        help="Extra argument appended to scripts/run_evaluation.sh; repeat as needed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cwd = Path.cwd()
    cann_bench_root = _resolve_path(args.cann_bench_root, base=cwd)
    source_dir = _resolve_path(args.source_dir, base=cwd)
    reports_dir = _resolve_path(args.reports_dir, base=cwd)
    reports_dir.mkdir(parents=True, exist_ok=True)

    if not cann_bench_root.is_dir():
        print(json.dumps({"overall_score": 0.0, "error": f"missing CANNBench root: {cann_bench_root}"}, sort_keys=True))
        return 2
    if not source_dir.is_dir():
        print(json.dumps({"overall_score": 0.0, "error": f"missing source dir: {source_dir}"}, sort_keys=True))
        return 2

    command = build_command(args, source_dir=source_dir, reports_dir=reports_dir)
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(cann_bench_root),
        text=True,
        capture_output=True,
        check=False,
    )
    elapsed = time.perf_counter() - started

    log_path = reports_dir / "cannbench_eval_command.log"
    log_path.write_text(
        "\n".join(
            [
                f"$ {' '.join(command)}",
                f"cwd: {cann_bench_root}",
                f"returncode: {completed.returncode}",
                "",
                "## stdout",
                completed.stdout,
                "## stderr",
                completed.stderr,
            ]
        ),
        encoding="utf-8",
    )

    report_path = _select_report_json(reports_dir)
    if report_path is None:
        metrics = {
            "overall_score": 0.0,
            "error": "CANNBench did not produce a JSON report",
            "cannbench_returncode": completed.returncode,
            "elapsed_seconds": elapsed,
            "log_path": str(log_path),
        }
        print(json.dumps(metrics, sort_keys=True))
        return completed.returncode or 1

    try:
        metrics = summarize_report(
            report_path,
            elapsed_seconds=elapsed,
            cannbench_returncode=completed.returncode,
        )
        metrics["log_path"] = str(log_path)
    except Exception as exc:
        metrics = {
            "overall_score": 0.0,
            "error": f"failed to parse CANNBench report: {exc}",
            "cannbench_returncode": completed.returncode,
            "elapsed_seconds": elapsed,
            "report_json": str(report_path),
            "log_path": str(log_path),
        }
        print(json.dumps(metrics, sort_keys=True))
        return completed.returncode or 1

    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
