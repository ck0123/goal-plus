from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .cases import BenchmarkCase, case_from_dict, case_to_dict
from .datasets import load_benchmark_cases
from .reporting import compare_paper_results
from .runners.direct import run_direct_case
from .runners.search_runtime import run_search_case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run small reasoning benchmarks through direct or MCP Search modes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sample = subparsers.add_parser("sample")
    sample.add_argument("--benchmark", required=True)
    sample.add_argument("--split")
    sample.add_argument("--limit", type=int, default=1)
    sample.add_argument("--out")

    run_one = subparsers.add_parser("run-one")
    run_one.add_argument("--benchmark")
    run_one.add_argument("--split")
    run_one.add_argument("--case-json")
    run_one.add_argument("--mode", choices=["direct", "search"], required=True)
    run_one.add_argument("--prediction")
    run_one.add_argument("--root", default="benchmarks/runs")
    run_one.add_argument("--out")
    run_one.add_argument("--worker-backend", choices=["fixed", "pi-rpc"], default="fixed")
    run_one.add_argument("--fixed-answer")
    run_one.add_argument("--max-candidates", type=int, default=1)
    run_one.add_argument("--max-parallel", type=int, default=1)
    run_one.add_argument("--strategy", default="random")
    run_one.add_argument("--max-runtime-seconds", type=int, default=180)
    run_one.add_argument("--max-turns", type=int, default=6)
    run_one.add_argument("--pi-binary", default="pi")
    run_one.add_argument("--pi-provider")
    run_one.add_argument("--pi-model-id", default="gpt-5.4-mini")
    run_one.add_argument("--pi-thinking")

    compare = subparsers.add_parser("compare")
    compare.add_argument("--ours", required=True)
    compare.add_argument("--paper", required=True)
    compare.add_argument("--out")

    args = parser.parse_args(argv)
    if args.command == "sample":
        return _cmd_sample(args)
    if args.command == "run-one":
        return _cmd_run_one(args)
    if args.command == "compare":
        return _cmd_compare(args)
    parser.error("unknown command")
    return 2


def _cmd_sample(args: argparse.Namespace) -> int:
    cases = load_benchmark_cases(args.benchmark, split=args.split, limit=args.limit)
    payload = [case_to_dict(case) for case in cases]
    _write_json_or_stdout(payload, args.out)
    return 0


def _cmd_run_one(args: argparse.Namespace) -> int:
    case = _load_case(args)
    if args.mode == "direct":
        result = run_direct_case(case, prediction_text=args.prediction)
    else:
        result = run_search_case(
            case,
            root_dir=Path(args.root),
            worker_backend=args.worker_backend,
            fixed_answer=args.fixed_answer,
            max_candidates=args.max_candidates,
            max_parallel=args.max_parallel,
            strategy_name=args.strategy,
            max_runtime_seconds=args.max_runtime_seconds,
            max_turns=args.max_turns,
            pi_binary=args.pi_binary,
            pi_provider=args.pi_provider,
            pi_model_id=args.pi_model_id,
            pi_thinking=args.pi_thinking,
        )
    _write_json_or_stdout(result, args.out)
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    result = compare_paper_results(
        ours=_read_rows(Path(args.ours)),
        paper=_read_rows(Path(args.paper)),
    )
    _write_json_or_stdout(result, args.out)
    return 0


def _load_case(args: argparse.Namespace) -> BenchmarkCase:
    if args.case_json:
        parsed = json.loads(Path(args.case_json).read_text(encoding="utf-8"))
        if isinstance(parsed, list):
            if not parsed:
                raise SystemExit(f"case file is empty: {args.case_json}")
            parsed = parsed[0]
        if not isinstance(parsed, dict):
            raise SystemExit(f"case file must contain an object or non-empty list: {args.case_json}")
        return case_from_dict(parsed)
    if not args.benchmark:
        raise SystemExit("--benchmark is required when --case-json is not provided")
    cases = load_benchmark_cases(args.benchmark, split=args.split, limit=1)
    if not cases:
        raise SystemExit(f"no cases found for benchmark {args.benchmark}")
    return cases[0]


def _write_json_or_stdout(payload: Any, out: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _read_jsonl_text(text)
    return _coerce_rows(parsed)


def _coerce_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("paper_result"), dict):
            return [value["paper_result"]]
        return [value]
    if isinstance(value, list):
        rows: list[dict[str, Any]] = []
        for item in value:
            rows.extend(_coerce_rows(item))
        return rows
    return []


def _read_jsonl_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
