#!/usr/bin/env python3
"""Prepare a CANNBench TileLang-Ascend search workspace."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any


EXAMPLE_ROOT = Path(__file__).resolve().parent
VERIFIER_SOURCE = EXAMPLE_ROOT / "verifier" / "cannbench_eval.py"
REPO_ROOT = EXAMPLE_ROOT.parents[1]
OUTER_CODE_ROOT = REPO_ROOT.parents[2] if len(REPO_ROOT.parents) > 2 else REPO_ROOT.parent


def _unique_paths(paths: list[Path]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        value = str(path)
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _default_akg_root() -> str:
    env = os.environ.get("AKG_AGENTS_ROOT")
    if env:
        return env
    candidates = _unique_paths([
        Path.cwd() / "akg_agents",
        Path.cwd() / "akg" / "akg_agents",
        Path.cwd() / "code" / "akg" / "akg_agents",
        REPO_ROOT.parent / "akg_agents",
        REPO_ROOT.parent / "akg" / "akg_agents",
        OUTER_CODE_ROOT / "akg" / "akg_agents",
    ])
    for candidate in candidates:
        if Path(candidate).is_dir():
            return candidate
    return candidates[0]


def _default_cann_bench_root() -> str:
    env = os.environ.get("CANN_BENCH_ROOT")
    if env:
        return env
    candidates = _unique_paths([
        Path.cwd() / "cann-bench",
        Path.cwd() / "code" / "cann-bench",
        REPO_ROOT.parent / "cann-bench",
    ])
    for candidate in candidates:
        if Path(candidate).is_dir():
            return candidate
    return candidates[0]


def _missing_path_message(path: Path, label: str) -> str:
    if label == "CANNBench root":
        return (
            f"{label} not found: {path}\n"
            "Set CANN_BENCH_ROOT or pass --cann-bench-root after cloning:\n"
            "  git clone https://gitcode.com/cann/cann-bench.git cann-bench"
        )
    if label == "AKG TileLang-Ascend skills":
        return (
            f"{label} not found: {path}\n"
            "Set AKG_AGENTS_ROOT or pass --akg-agents-root after cloning:\n"
            "  git clone https://gitcode.com/mindspore/akg.git akg_agents\n"
            "Expected skill path:\n"
            "  <akg_agents>/python/akg_agents/op/resources/skills/tilelang-ascend"
        )
    return f"{label} not found: {path}"


def _snake_case(name: str) -> str:
    name = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.replace("-", "_").lower()


def _copytree(src: Path, dst: Path) -> None:
    ignored = {
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        "_cannbench_reports",
        "_compile.log",
    }

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignored or name.endswith((".pyc", ".pyo"))}

    shutil.copytree(src, dst, ignore=ignore)


def _task_files(task_dir: Path) -> list[Path]:
    names = ["proto.yaml", "cases.yaml", "cases.csv", "golden.py", "desc.md"]
    return [task_dir / name for name in names if (task_dir / name).is_file()]


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _search_spec(
    *,
    output_dir: Path,
    cann_bench_root: Path,
    task_dir_arg: str,
    operator: str,
    function_name: str,
    device_id: str | None,
    timeout_seconds: int,
    max_candidates: int,
    max_parallel: int,
) -> dict[str, Any]:
    command = [
        "python",
        "_verifier/cannbench_eval.py",
        "--cann-bench-root",
        str(cann_bench_root),
        "--task-dir",
        task_dir_arg,
        "--operator",
        operator,
        "--source-dir",
        ".",
        "--reports-dir",
        "_cannbench_reports",
    ]
    if device_id:
        command.extend(["--device-id", device_id])

    return {
        "objective": f"maximize CANNBench score for TileLang-Ascend {operator}",
        "metric_name": "overall_score",
        "metric_direction": "maximize",
        "source_path": str(output_dir),
        "edit_surface": {
            "allow": ["cann_bench/"],
            "deny": [
                "_verifier/",
                "_task/",
                "_skills/",
                "_cannbench_reports/",
                "build.sh",
                "setup.py",
                "dist/",
            ],
            "max_file_changes": 4,
        },
        "process_verifiers": [
            {
                "name": "cannbench_score",
                "role": "ranking_signal",
                "command": command,
                "timeout_seconds": timeout_seconds,
                "expected_outputs": ["_cannbench_reports/*.json"],
            }
        ],
        "promotion_verifiers": [
            {
                "name": "frozen_hash_gate",
                "role": "anti_cheat_gate",
                "command": ["search-runtime-internal", "check-frozen-hashes"],
            }
        ],
        "budget": {
            "max_candidates": max_candidates,
            "max_parallel": max_parallel,
        },
        "strategy": {
            "name": "agent_guided",
            "driver": "builtin",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {
                "max_runtime_seconds": timeout_seconds,
                "max_turns": 30,
                "on_exceed": "interrupt",
            },
            "history_policy": {"scope": "top_n", "top_n": 5},
        },
        "constraints": {
            "backend": "tilelang_ascend",
            "cann_bench_root": str(cann_bench_root),
            "task_dir": task_dir_arg,
            "operator": operator,
            "function_name": function_name,
            "candidate_contract": (
                f"Expose cann_bench.{function_name} with the schema from _task/proto.yaml; "
                "use TileLang-Ascend only, not AscendC."
            ),
            "suggested_batch_size": max_parallel,
        },
        "root_hypotheses": [
            "baseline TileLang example adapted to the CANNBench schema",
            "shape-specialized tiling for the largest CANNBench cases",
            "padding/cropping strategy for non-divisible dimensions",
            "autotune or small config search inside the Python wrapper",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cann-bench-root", default=_default_cann_bench_root())
    parser.add_argument("--akg-agents-root", default=_default_akg_root())
    parser.add_argument("--task-dir", default="bench_lab/tilelang_ascend_bench/gemm")
    parser.add_argument("--operator", default="Gemm")
    parser.add_argument("--function-name")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--spec-out")
    parser.add_argument("--device-id")
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--max-candidates", type=int, default=4)
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cann_bench_root = Path(args.cann_bench_root).expanduser().resolve()
    akg_root = Path(args.akg_agents_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    spec_out = Path(args.spec_out).expanduser().resolve() if args.spec_out else output_dir.with_suffix(".gp_spec.json")
    function_name = args.function_name or _snake_case(args.operator)

    source_template = cann_bench_root / "examples" / "tilelang_cann_example"
    task_dir = Path(args.task_dir)
    task_path = task_dir if task_dir.is_absolute() else cann_bench_root / task_dir
    skill_root = akg_root / "python" / "akg_agents" / "op" / "resources" / "skills" / "tilelang-ascend"

    for path, label in [
        (cann_bench_root, "CANNBench root"),
        (task_path, "CANNBench task dir"),
        (source_template, "CANNBench TileLang example"),
        (skill_root, "AKG TileLang-Ascend skills"),
        (VERIFIER_SOURCE, "cannbench_eval.py"),
    ]:
        if not path.exists():
            raise FileNotFoundError(_missing_path_message(path, label))

    if output_dir.exists():
        if not args.force:
            raise FileExistsError(f"output already exists, pass --force to replace: {output_dir}")
        shutil.rmtree(output_dir)

    _copytree(source_template, output_dir)
    (output_dir / "_verifier").mkdir(parents=True, exist_ok=True)
    shutil.copy2(VERIFIER_SOURCE, output_dir / "_verifier" / "cannbench_eval.py")

    task_copy = output_dir / "_task"
    task_copy.mkdir(parents=True, exist_ok=True)
    for file_path in _task_files(task_path):
        shutil.copy2(file_path, task_copy / file_path.name)

    skills_copy = output_dir / "_skills" / "tilelang-ascend"
    shutil.copytree(skill_root, skills_copy)

    _write_text(
        output_dir / "_task" / "TASK.md",
        "\n".join(
            [
                f"# CANNBench TileLang-Ascend Task: {args.operator}",
                "",
                f"- CANNBench root: `{cann_bench_root}`",
                f"- Task dir: `{args.task_dir}`",
                f"- Operator: `{args.operator}`",
                f"- Required Python API: `cann_bench.{function_name}`",
                "- Backend: `tilelang_ascend` only. Do not switch to AscendC or Triton-Ascend.",
                "",
                "Read `_task/proto.yaml`, `_task/desc.md`, and `_task/cases.yaml` before editing.",
                "Read `_skills/tilelang-ascend/fundamentals/*.md` and the matching guide/example skill for this operator family.",
                "Run `search_run_verifier` after the first complete implementation and after each meaningful optimization.",
                "",
            ]
        ),
    )

    spec = _search_spec(
        output_dir=output_dir,
        cann_bench_root=cann_bench_root,
        task_dir_arg=args.task_dir,
        operator=args.operator,
        function_name=function_name,
        device_id=args.device_id,
        timeout_seconds=args.timeout_seconds,
        max_candidates=args.max_candidates,
        max_parallel=args.max_parallel,
    )
    _write_text(spec_out, json.dumps(spec, indent=2, ensure_ascii=False) + "\n")

    verifier_artifacts = [
        output_dir / "_verifier" / "cannbench_eval.py",
        output_dir / "_task" / "TASK.md",
        *sorted(task_copy.glob("*")),
    ]
    artifact_out = spec_out.with_suffix(".verifier_artifacts.json")
    _write_text(
        artifact_out,
        json.dumps([str(path) for path in verifier_artifacts if path.is_file()], indent=2, ensure_ascii=False) + "\n",
    )

    print(json.dumps({
        "workspace": str(output_dir),
        "search_spec": str(spec_out),
        "verifier_artifacts": str(artifact_out),
        "skill_root": str(skill_root),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
