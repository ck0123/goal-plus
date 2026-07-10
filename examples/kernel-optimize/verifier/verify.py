#!/usr/bin/env python3
"""Correctness verifier for the kernel-optimize example template.

Compares a PyTorch reference (`Model`) against a candidate implementation
(`ModelNew`) across one or more input groups. Multi-shape aware: each shape
runs in its own try/except, all shapes run before the result is flushed.

File convention under <verify_dir>/:
  {op_name}_torch.py        defines Model, get_init_inputs(),
                            and get_inputs() or get_input_groups()
  {op_name}_{impl_name}.py  defines ModelNew

Output: <verify_dir>/verify_result.json (or --output path):
  {op_name, total_cases, passed_cases, failed_cases, failures[]}

Exit codes:
  0  passed_cases == total_cases > 0
  1  at least one shape failed (Strategy A: strict)
  2  script could not run (verify_dir missing, modules unimportable, etc.)

Usage:
  python verify.py --op_name <op> --verify_dir <dir> [--impl_name impl] [--output <path>]
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import traceback
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common_utils import (  # noqa: E402
    cleanup_device_memory,
    compare_tensors,
    describe_input,
    resolve_input_provider,
    select_device,
    truncate,
)

logger = logging.getLogger("kernel_optimize.verify")


def _setup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)


@dataclass
class _CaseCtx:
    idx: int
    total: int


def _load_modules(op_name: str, verify_dir: str, impl_name: str):
    sys.path.insert(0, verify_dir)
    torch_mod = importlib.import_module(f"{op_name}_torch")
    impl_mod = importlib.import_module(f"{op_name}_{impl_name}")
    return torch_mod, impl_mod


def _instantiate(torch_cls, impl_cls, get_init_inputs, device):
    import torch

    init = get_init_inputs()
    torch.manual_seed(0)
    fw = torch_cls(*init).to(device)
    torch.manual_seed(0)
    impl = impl_cls(*init).to(device)
    return fw, impl


def _run_case(fw_model, impl_model, inputs, device, case: _CaseCtx) -> None:
    """Run one input group; raise AssertionError on any disagreement."""
    import torch

    def _to_device(args):
        return [x.to(device) if isinstance(x, torch.Tensor) else x for x in args]

    inputs_fw = _to_device(inputs)
    inputs_impl = _to_device(inputs)

    with torch.no_grad():
        fw_out = fw_model(*inputs_fw)
        impl_out = impl_model(*inputs_impl)

    if not isinstance(fw_out, (list, tuple)):
        fw_out = [fw_out]
    if not isinstance(impl_out, (list, tuple)):
        impl_out = [impl_out]

    if len(fw_out) != len(impl_out):
        raise AssertionError(
            f"[case {case.idx}/{case.total}] output count mismatch: fw={len(fw_out)}, impl={len(impl_out)}"
        )

    for i, (g, a) in enumerate(zip(fw_out, impl_out)):
        if g is None or a is None:
            raise AssertionError(
                f"[case {case.idx}/{case.total}] output {i} is None: fw={g is None}, impl={a is None}"
            )
        if isinstance(g, torch.Tensor) and isinstance(a, torch.Tensor):
            try:
                compare_tensors(g, a, g.dtype)
            except AssertionError as e:
                raise AssertionError(f"[case {case.idx}/{case.total}] {e}") from e


def _try_case(torch_cls, impl_cls, get_init_inputs, inputs, device, case: _CaseCtx):
    fw_model = None
    impl_model = None
    try:
        fw_model, impl_model = _instantiate(torch_cls, impl_cls, get_init_inputs, device)
        _run_case(fw_model, impl_model, inputs, device, case)
        return True, None
    except Exception as e:
        err = traceback.format_exc()
        logger.error("[case %d/%d] FAILED: %s: %s", case.idx, case.total, type(e).__name__, e)
        return False, {
            "case_idx": case.idx,
            "input_desc": describe_input(inputs),
            "error_type": type(e).__name__,
            "error_msg": truncate(err),
        }
    finally:
        del fw_model, impl_model
        cleanup_device_memory()


def verify(op_name: str, verify_dir: str, impl_name: str = "impl", output: str | None = None) -> tuple[int, int]:
    torch_mod, impl_mod = _load_modules(op_name, verify_dir, impl_name)
    device, _ = select_device()
    logger.info("using device: %s", device)

    input_groups, total = resolve_input_provider(torch_mod)
    failures: list[dict] = []
    passed = 0

    for idx, inputs in enumerate(input_groups, start=1):
        ok, failure = _try_case(
            torch_mod.Model,
            impl_mod.ModelNew,
            torch_mod.get_init_inputs,
            inputs,
            device,
            _CaseCtx(idx, total),
        )
        if ok:
            passed += 1
        else:
            failures.append(failure)

    failed = total - passed
    out_path = output or os.path.join(verify_dir, "verify_result.json")
    result = {
        "op_name": op_name,
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": failed,
        "failures": failures,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info("verify_result.json written to %s", out_path)

    if failed == 0:
        logger.info("PASS: %d/%d cases", passed, total)
    else:
        logger.error("FAIL: %d/%d cases passed, %d failed", passed, total, failed)
    return passed, total


def main() -> None:
    _setup()
    parser = argparse.ArgumentParser(description="kernel-optimize correctness verifier")
    parser.add_argument("--op_name", required=True)
    parser.add_argument("--verify_dir", required=True)
    parser.add_argument("--impl_name", default="impl")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    verify_dir = os.path.abspath(args.verify_dir)
    if not os.path.isdir(verify_dir):
        logger.error("verify_dir does not exist: %s", verify_dir)
        sys.exit(2)

    try:
        passed, total = verify(args.op_name, verify_dir, args.impl_name, args.output)
    except Exception:
        logger.error("verify crashed:\n%s", traceback.format_exc())
        sys.exit(2)
    sys.exit(0 if passed == total and total > 0 else 1)


if __name__ == "__main__":
    main()
