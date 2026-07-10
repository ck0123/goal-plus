"""Shared helpers for kernel-optimize verifier scripts (verify.py, benchmark.py).

Pure stdlib + torch. NPU sync paths are gated on `hasattr(torch, "npu")` so
the same files run on CPU-only machines and on NPU boxes without changes.
"""
from __future__ import annotations

import gc
import math
from typing import Any, Iterable


_STR_THRESHOLD = {
    "float16": 2 ** -10,
    "bfloat16": 2 ** -7,
    "float32": 2 ** -13,
    "hifloat32": 2 ** -11,
    "float8_e4m3": 2 ** -3,
    "float8_e5m2": 2 ** -2,
    "fp8_e4m3": 2 ** -3,
    "fp8_e5m2": 2 ** -2,
}


def dtype_threshold(dtype: Any) -> float:
    """Return the MERE threshold for a torch dtype or dtype-name string.

    MARE upper bound is 10 * threshold; the caller multiplies.
    """
    import torch

    if isinstance(dtype, str):
        return _STR_THRESHOLD.get(dtype.lower(), 2 ** -13)

    mapping = {
        torch.float16: 2 ** -10,
        torch.bfloat16: 2 ** -7,
        torch.float32: 2 ** -13,
    }
    for attr in ("float8_e4m3fn", "float8_e4m3"):
        dt = getattr(torch, attr, None)
        if dt is not None:
            mapping[dt] = 2 ** -3
    for attr in ("float8_e5m2fn", "float8_e5m2"):
        dt = getattr(torch, attr, None)
        if dt is not None:
            mapping[dt] = 2 ** -2
    return mapping.get(dtype, 2 ** -13)


def compare_tensors(golden, actual, dtype) -> None:
    """Raise AssertionError when `golden` and `actual` disagree.

    Pre-checks (any failure raises): shape equality, NaN-position equality,
    Inf-position + sign equality. Then MERE < t and MARE < 10t over the finite
    mask, where t is the dtype threshold. Bool dtype uses torch.equal.
    """
    import torch

    g = golden.detach().cpu().flatten()
    a = actual.detach().cpu().flatten()
    if a.dtype != g.dtype:
        a = a.to(g.dtype)

    if g.shape != a.shape:
        raise AssertionError(f"shape mismatch: golden={tuple(g.shape)}, actual={tuple(a.shape)}")

    if not torch.equal(torch.isnan(g), torch.isnan(a)):
        raise AssertionError("NaN positions do not match")
    if not torch.equal(torch.isinf(g), torch.isinf(a)):
        raise AssertionError("Inf positions do not match")
    if torch.isinf(g).any():
        if not torch.equal(torch.sign(g[torch.isinf(g)]), torch.sign(a[torch.isinf(a)])):
            raise AssertionError("Inf signs do not match")

    finite = torch.isfinite(g) & torch.isfinite(a)
    if finite.sum().item() == 0:
        return

    gf = g[finite].float()
    af = a[finite].float()

    if gf.dtype == torch.bool:
        if not torch.equal(gf, af):
            raise AssertionError("bool values do not match")
        return

    t = dtype_threshold(dtype)
    rel = (af - gf).abs() / gf.abs().clamp(min=t)
    mere = rel.mean().item()
    mare = rel.max().item()
    if not (mere < t and mare < 10 * t):
        bad = torch.where(rel > t)[0][:10].tolist()
        raise AssertionError(
            f"accuracy mismatch: dtype={dtype}, mere={mere:.4e}, mare={mare:.4e}, "
            f"threshold={t:.4e}, bad_idx_first10={bad}"
        )


def describe_input(inputs: Iterable[Any]) -> list[dict[str, Any]]:
    """Structured description of an input tuple, for JSON reporting."""
    try:
        import torch
    except Exception:
        torch = None

    out: list[dict[str, Any]] = []
    for x in inputs:
        if torch is not None and isinstance(x, torch.Tensor):
            out.append({"type": "tensor", "shape": list(x.shape), "dtype": str(x.dtype)})
        else:
            try:
                v = x if isinstance(x, (int, float, bool, str)) else repr(x)
            except Exception:
                v = "<unrepr>"
            out.append({"type": "scalar", "value": v})
    return out


def classify_speedup(s: Any) -> str:
    """Bucket a speedup value: none | nan | inf | negative | zero | valid."""
    if s is None or not isinstance(s, (int, float)):
        return "none"
    if math.isnan(s):
        return "nan"
    if math.isinf(s):
        return "inf"
    if s < 0:
        return "negative"
    if s == 0:
        return "zero"
    return "valid"


def geomean(values: list[float]) -> float | None:
    if not values:
        return None
    return math.exp(sum(math.log(v) for v in values) / len(values))


def select_device():
    """Return (device, sync_callable). Prefers NPU; falls back to CPU.

    The returned sync is a no-op on CPU so callers can invoke unconditionally.
    """
    import torch

    npu = getattr(torch, "npu", None)
    if npu is not None and callable(getattr(npu, "is_available", None)) and npu.is_available():
        return torch.device("npu"), npu.synchronize
    return torch.device("cpu"), lambda: None


def resolve_input_provider(torch_module):
    """Return (input_groups, total_cases).

    Accepts `get_input_groups()` (multi-shape) or `get_inputs()` (single).
    """
    if hasattr(torch_module, "get_input_groups"):
        groups = torch_module.get_input_groups()
        return groups, len(groups)
    if hasattr(torch_module, "get_inputs"):
        return [torch_module.get_inputs()], 1
    raise AttributeError("reference module must define get_inputs() or get_input_groups()")


def truncate(msg: str, limit: int = 2000) -> str:
    if len(msg) <= limit:
        return msg
    half = limit // 2
    return f"{msg[:half]}\n... [truncated {len(msg) - limit} chars] ...\n{msg[-half:]}"


def cleanup_device_memory() -> None:
    """Best-effort device-memory cleanup. CPU-bound runs are a no-op."""
    try:
        import torch
        npu = getattr(torch, "npu", None)
        if npu is not None and hasattr(npu, "empty_cache"):
            npu.empty_cache()
    except Exception:
        pass
    gc.collect()
