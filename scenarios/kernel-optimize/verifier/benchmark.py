#!/usr/bin/env python3
"""Latency benchmark for the kernel-optimize scenario.

Measures framework (`Model`) vs implementation (`ModelNew`) latency on NPU
(when `torch.npu` is available, via `torch.npu.synchronize` + perf_counter)
or CPU (perf_counter only). Reports geometric-mean speedup across shapes
that passed verification.

L1 gate: refuses to run if `{verify_dir}/verify_result.json` (or
`verify_result_{impl_name}.json` for non-default impl_name) does not show
`passed_cases == total_cases`. Use --verify_not_required to bypass.

Output JSON (printed and written to --output if given):
  {
    op_name, warmup, repeats, total_cases, passed_cases, failed_cases,
    nan_indices, inf_indices, zero_indices, negative_indices, none_indices,
    framework:        {avg_latency_ms, peak_memory_mb, operators: {}},
    implementation:   {avg_latency_ms, peak_memory_mb, operators: {}},
    speedup_vs_torch: <float or null>,
    per_shape_results: [
      {case_idx, input_desc, status, framework, implementation,
       speedup_vs_torch, error_type, error_msg}
    ]
  }

Exit codes:
  0  benchmark ran; read JSON for pass/fail
  1  script crash
  2  L1 gate refused (verify_result missing or not fully passed)

Usage:
  python benchmark.py --op_name <op> --verify_dir <dir> [--impl_name impl]
                      [--warmup 5] [--repeats 50] [--output <path>]
                      [--verify_not_required]
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common_utils import (  # noqa: E402
    classify_speedup,
    cleanup_device_memory,
    describe_input,
    geomean,
    resolve_input_provider,
    select_device,
    truncate,
)

logger = logging.getLogger("kernel_optimize.benchmark")
IMPL_DEFAULT = "impl"


def _setup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)


@dataclass
class _CaseCtx:
    idx: int
    total: int


@dataclass
class _PerfResult:
    avg_latency_ms: float
    peak_memory_mb: float


@dataclass
class _ShapeResult:
    case_idx: int
    input_desc: list[dict[str, Any]]
    status: str = "pass"
    framework: _PerfResult | None = None
    implementation: _PerfResult | None = None
    speedup_vs_torch: float | None = None
    error_type: str | None = None
    error_msg: str | None = None


def _verify_json_name(impl_name: str) -> str:
    if impl_name == IMPL_DEFAULT:
        return "verify_result.json"
    return f"verify_result_{impl_name}.json"


def _verify_gate(verify_dir: str, impl_name: str) -> None:
    """Raise RuntimeError if the verify_result file is missing or not fully passed."""
    path = os.path.join(verify_dir, _verify_json_name(impl_name))
    if not os.path.isfile(path):
        raise RuntimeError(
            f"[L1 gate] verify_result not found: {path}; "
            "run verify.py first or pass --verify_not_required"
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"[L1 gate] verify_result unreadable: {path}: {e}") from e
    total = data.get("total_cases", 0)
    passed = data.get("passed_cases", 0)
    if total == 0:
        raise RuntimeError(f"[L1 gate] verify_result total_cases=0: {path}")
    if passed != total:
        failures = data.get("failures", []) or []
        head = "; ".join(
            f"case {f.get('case_idx')}: {f.get('error_type')}" for f in failures[:5]
        )
        raise RuntimeError(
            f"[L1 gate] verify_result {passed}/{total}: {path}; first failures: {head}"
        )


def _load_modules(op_name: str, verify_dir: str, impl_name: str):
    sys.path.insert(0, verify_dir)
    torch_mod = importlib.import_module(f"{op_name}_torch")
    impl_mod = importlib.import_module(f"{op_name}_{impl_name}")
    return torch_mod, impl_mod


def _instantiate(cls, get_init_inputs, device):
    import torch

    init = get_init_inputs()
    torch.manual_seed(0)
    return cls(*init).to(device)


def _to_device(inputs, device):
    import torch

    return [x.to(device) if isinstance(x, torch.Tensor) else x for x in inputs]


def _reset_peak_memory() -> None:
    try:
        import torch
        npu = getattr(torch, "npu", None)
        if npu is not None and hasattr(npu, "reset_peak_memory_stats"):
            npu.reset_peak_memory_stats()
    except Exception:
        pass


def _peak_memory_mb() -> float:
    try:
        import torch
        npu = getattr(torch, "npu", None)
        if npu is not None and hasattr(npu, "max_memory_allocated"):
            return npu.max_memory_allocated() / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def _measure(model, inputs, warmup: int, repeats: int, sync) -> _PerfResult:
    """Warmup + timed loop. `sync` is a callable or no-op for CPU."""
    import torch

    with torch.no_grad():
        for _ in range(warmup):
            model(*inputs)
    sync()

    lats: list[float] = []
    for _ in range(repeats):
        sync()
        start = time.perf_counter()
        with torch.no_grad():
            model(*inputs)
        sync()
        end = time.perf_counter()
        lats.append((end - start) * 1000)

    avg_ms = statistics.mean(lats)
    peak_mb = _peak_memory_mb()
    return _PerfResult(avg_latency_ms=round(avg_ms, 4), peak_memory_mb=round(peak_mb, 2))


def _run_shape(torch_cls, impl_cls, get_init_inputs, inputs, device, sync,
               warmup: int, repeats: int, case: _CaseCtx) -> _ShapeResult:
    fw_model = None
    impl_model = None
    try:
        fw_model = _instantiate(torch_cls, get_init_inputs, device)
        impl_model = _instantiate(impl_cls, get_init_inputs, device)
        inputs_d = _to_device(inputs, device)

        fw_perf = _measure(fw_model, inputs_d, warmup, repeats, sync)
        _reset_peak_memory()
        impl_perf = _measure(impl_model, inputs_d, warmup, repeats, sync)

        speedup = (
            fw_perf.avg_latency_ms / impl_perf.avg_latency_ms
            if impl_perf.avg_latency_ms > 0
            else 0.0
        )
        return _ShapeResult(
            case_idx=case.idx,
            input_desc=describe_input(inputs),
            framework=fw_perf,
            implementation=impl_perf,
            speedup_vs_torch=round(speedup, 4),
        )
    except Exception as e:
        err = traceback.format_exc()
        logger.error("[case %d/%d] FAILED: %s: %s", case.idx, case.total, type(e).__name__, e)
        return _ShapeResult(
            case_idx=case.idx,
            input_desc=describe_input(inputs),
            status="fail",
            error_type=type(e).__name__,
            error_msg=truncate(err),
        )
    finally:
        del fw_model, impl_model
        cleanup_device_memory()


def _aggregate(shapes: list[_ShapeResult]) -> tuple[dict | None, dict | None, float | None,
                                                    list[int], list[int], list[int], list[int], list[int]]:
    valid_speedups: list[float] = []
    nan_i: list[int] = []
    inf_i: list[int] = []
    zero_i: list[int] = []
    neg_i: list[int] = []
    none_i: list[int] = []
    fw_lats: list[float] = []
    impl_lats: list[float] = []
    fw_mems: list[float] = []
    impl_mems: list[float] = []

    for s in shapes:
        if s.status != "pass":
            continue
        cat = classify_speedup(s.speedup_vs_torch)
        if cat == "valid" and s.framework and s.implementation:
            valid_speedups.append(s.speedup_vs_torch)  # type: ignore[arg-type]
            fw_lats.append(s.framework.avg_latency_ms)
            impl_lats.append(s.implementation.avg_latency_ms)
            fw_mems.append(s.framework.peak_memory_mb)
            impl_mems.append(s.implementation.peak_memory_mb)
        elif cat == "nan":
            nan_i.append(s.case_idx)
        elif cat == "inf":
            inf_i.append(s.case_idx)
        elif cat == "zero":
            zero_i.append(s.case_idx)
        elif cat == "negative":
            neg_i.append(s.case_idx)
        else:
            none_i.append(s.case_idx)

    n = len(valid_speedups)
    if n > 0:
        fw_agg = {
            "avg_latency_ms": round(sum(fw_lats) / n, 4),
            "peak_memory_mb": round(sum(fw_mems) / n, 2),
            "operators": {},
        }
        impl_agg = {
            "avg_latency_ms": round(sum(impl_lats) / n, 4),
            "peak_memory_mb": round(sum(impl_mems) / n, 2),
            "operators": {},
        }
        gm = geomean(valid_speedups)
        overall = round(gm, 4) if gm is not None else None
    else:
        fw_agg = None
        impl_agg = None
        overall = None

    return fw_agg, impl_agg, overall, nan_i, inf_i, zero_i, neg_i, none_i


def benchmark(op_name: str, verify_dir: str, impl_name: str,
              warmup: int, repeats: int, output: str | None = None) -> dict:
    torch_mod, impl_mod = _load_modules(op_name, verify_dir, impl_name)
    device, sync = select_device()
    logger.info("using device: %s", device)

    input_groups, total = resolve_input_provider(torch_mod)
    shapes: list[_ShapeResult] = []
    for idx, inputs in enumerate(input_groups, start=1):
        shapes.append(
            _run_shape(
                torch_mod.Model, impl_mod.ModelNew, torch_mod.get_init_inputs,
                inputs, device, sync, warmup, repeats, _CaseCtx(idx, total),
            )
        )

    passed = sum(1 for s in shapes if s.status == "pass")
    failed = total - passed

    fw_agg, impl_agg, overall, nan_i, inf_i, zero_i, neg_i, none_i = _aggregate(shapes)

    result = {
        "op_name": op_name,
        "warmup": warmup,
        "repeats": repeats,
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": failed,
        "nan_indices": nan_i,
        "inf_indices": inf_i,
        "zero_indices": zero_i,
        "negative_indices": neg_i,
        "none_indices": none_i,
        "framework": fw_agg,
        "implementation": impl_agg,
        "speedup_vs_torch": overall,
        "per_shape_results": [
            {
                "case_idx": s.case_idx,
                "input_desc": s.input_desc,
                "status": s.status,
                "framework": (
                    {"avg_latency_ms": s.framework.avg_latency_ms, "peak_memory_mb": s.framework.peak_memory_mb}
                    if s.framework else None
                ),
                "implementation": (
                    {"avg_latency_ms": s.implementation.avg_latency_ms, "peak_memory_mb": s.implementation.peak_memory_mb}
                    if s.implementation else None
                ),
                "speedup_vs_torch": (
                    s.speedup_vs_torch if classify_speedup(s.speedup_vs_torch) == "valid" else None
                ),
                "error_type": s.error_type,
                "error_msg": s.error_msg,
            }
            for s in shapes
        ],
    }

    out_path = output or os.path.join(verify_dir, "perf_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info("perf_result.json written to %s", out_path)
    logger.info("passed %d/%d, speedup_vs_torch=%s", passed, total, overall)
    return result


def main() -> None:
    _setup()
    parser = argparse.ArgumentParser(description="kernel-optimize latency benchmark")
    parser.add_argument("--op_name", required=True)
    parser.add_argument("--verify_dir", required=True)
    parser.add_argument("--impl_name", default=IMPL_DEFAULT)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=50)
    parser.add_argument("--output", default=None)
    parser.add_argument("--verify_not_required", action="store_true")
    args = parser.parse_args()

    verify_dir = os.path.abspath(args.verify_dir)
    if not os.path.isdir(verify_dir):
        logger.error("verify_dir does not exist: %s", verify_dir)
        sys.exit(2)

    if not args.verify_not_required:
        try:
            _verify_gate(verify_dir, args.impl_name)
        except RuntimeError as e:
            logger.error(str(e))
            sys.exit(2)
    else:
        logger.warning("[L1 gate] skipped via --verify_not_required")

    try:
        benchmark(args.op_name, verify_dir, args.impl_name,
                  args.warmup, args.repeats, args.output)
    except Exception:
        logger.error("benchmark crashed:\n%s", traceback.format_exc())
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
