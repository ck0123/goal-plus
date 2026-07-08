from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_PATH = ROOT / "scenarios" / "kernel-optimize" / "verifier" / "benchmark.py"


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("kernel_optimize_benchmark", BENCHMARK_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_kernel_optimize_benchmark_prints_runtime_metric_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    benchmark = _load_benchmark_module()

    def fake_benchmark(*args, **kwargs):
        return {
            "op_name": "matmul",
            "total_cases": 1,
            "passed_cases": 1,
            "failed_cases": 0,
            "framework": {"avg_latency_ms": 3.0, "peak_memory_mb": 0.0},
            "implementation": {"avg_latency_ms": 1.5, "peak_memory_mb": 0.0},
            "speedup_vs_torch": 2.0,
        }

    monkeypatch.setattr(benchmark, "benchmark", fake_benchmark)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark.py",
            "--op_name",
            "matmul",
            "--verify_dir",
            str(tmp_path),
            "--verify_not_required",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        benchmark.main()

    assert exc.value.code == 0
    last_line = [line for line in capsys.readouterr().out.splitlines() if line][-1]
    metrics = json.loads(last_line)
    assert metrics["avg_latency_ms"] == 1.5
    assert metrics["framework_avg_latency_ms"] == 3.0
    assert metrics["speedup_vs_torch"] == 2.0
    assert metrics["passed_cases"] == 1
