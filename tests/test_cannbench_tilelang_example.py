from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from agentic_any_search_mcp.models import SearchSpec


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "cannbench-tilelang-ascend"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_cannbench_eval_summarizes_latest_report(tmp_path: Path) -> None:
    module = _load_module(EXAMPLE_DIR / "verifier" / "cannbench_eval.py", "cannbench_eval_example")
    reports = tmp_path / "reports"
    reports.mkdir()
    report = reports / "cann_performance.json"
    report.write_text(
        json.dumps(
            {
                "overall_score": 42.5,
                "total_operators": 1,
                "total_cases": 20,
                "passed_cases": 18,
                "summary": {"pass_rate": 0.9},
                "operators": [
                    {
                        "operator": "Gemm",
                        "rel_path": "gemm",
                        "total_score": 42.5,
                        "compile_passed": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    selected = module._select_report_json(reports)
    assert selected == report
    metrics = module.summarize_report(report, elapsed_seconds=1.25, cannbench_returncode=0)

    assert metrics["overall_score"] == 42.5
    assert metrics["pass_rate"] == 0.9
    assert metrics["operator_report"]["operator"] == "Gemm"
    assert metrics["operator_report"]["total_score"] == 42.5


def test_prepare_workspace_generates_pi_rpc_search_spec(tmp_path: Path) -> None:
    cann_root = tmp_path / "cann-bench"
    template = cann_root / "examples" / "tilelang_cann_example"
    _write(template / "build.sh", "#!/bin/bash\n")
    _write(template / "setup.py", "from setuptools import setup\n")
    _write(template / "cann_bench" / "__init__.py", "__version__ = '1.0.0'\n")
    _write(template / "cann_bench" / "_common.py", "PASS_CONFIGS = {}\n")

    task_dir = cann_root / "bench_lab" / "tilelang_ascend_bench" / "gemm"
    _write(task_dir / "proto.yaml", "operator:\n  name: Gemm\n  schema: gemm(Tensor A, Tensor B) -> Tensor C\n")
    _write(task_dir / "cases.yaml", "cases: []\n")
    _write(task_dir / "cases.csv", "case_id\n")
    _write(task_dir / "golden.py", "def gemm(A, B):\n    return A @ B\n")
    _write(task_dir / "desc.md", "# Gemm\n")

    akg_root = tmp_path / "akg_agents"
    _write(
        akg_root
        / "python"
        / "akg_agents"
        / "op"
        / "resources"
        / "skills"
        / "tilelang-ascend"
        / "fundamentals"
        / "tilelang-ascend-basics"
        / "SKILL.md",
        "---\nname: tilelang-ascend-basics\n---\n",
    )

    workspace = tmp_path / "workspace"
    spec_out = tmp_path / "workspace.search_spec.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(EXAMPLE_DIR / "prepare_workspace.py"),
            "--cann-bench-root",
            str(cann_root),
            "--akg-agents-root",
            str(akg_root),
            "--task-dir",
            "bench_lab/tilelang_ascend_bench/gemm",
            "--operator",
            "Gemm",
            "--function-name",
            "gemm",
            "--output-dir",
            str(workspace),
            "--spec-out",
            str(spec_out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["workspace"] == str(workspace.resolve())
    assert (workspace / "_verifier" / "cannbench_eval.py").is_file()
    assert (workspace / "_task" / "TASK.md").is_file()
    assert (workspace / "_skills" / "tilelang-ascend").is_dir()

    spec = SearchSpec.model_validate(json.loads(spec_out.read_text(encoding="utf-8")))
    assert spec.metric_name == "overall_score"
    assert spec.strategy.worker_host == "pi-rpc"
    assert spec.strategy.worker_budget is not None
    assert spec.constraints["backend"] == "tilelang_ascend"
    assert spec.edit_surface.allow == ["cann_bench/"]
    assert "_skills/" in spec.edit_surface.deny

    artifacts_path = spec_out.with_suffix(".verifier_artifacts.json")
    artifacts = json.loads(artifacts_path.read_text(encoding="utf-8"))
    assert str(workspace / "_verifier" / "cannbench_eval.py") in artifacts
    assert str(workspace / "_task" / "TASK.md") in artifacts
