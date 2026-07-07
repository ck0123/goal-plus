from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..cases import BenchmarkCase


WorkerHost = Literal["opencode", "codex", "claude-code", "pi-rpc"]


@dataclass(frozen=True)
class CaseWorkspace:
    case: BenchmarkCase
    source_path: Path
    verifier_gold_path: Path


def safe_case_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def prepare_case_workspace(case: BenchmarkCase, root: Path) -> CaseWorkspace:
    case_dir = root / safe_case_id(case.task_id)
    gold_dir = root / "_gold"
    case_dir.mkdir(parents=True, exist_ok=True)
    gold_dir.mkdir(parents=True, exist_ok=True)

    (case_dir / "QUESTION.md").write_text(_render_question(case), encoding="utf-8")
    (case_dir / "README.md").write_text(_render_instructions(case), encoding="utf-8")
    (case_dir / "answer.json").write_text(
        json.dumps({"answer": ""}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    gold_path = gold_dir / f"{safe_case_id(case.task_id)}.json"
    gold_path.write_text(
        json.dumps(
            {
                "benchmark": case.benchmark,
                "task_id": case.task_id,
                "answer_type": case.answer_type,
                "gold": case.gold,
                "choices": [choice.__dict__ for choice in case.choices],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return CaseWorkspace(case=case, source_path=case_dir, verifier_gold_path=gold_path)


def build_search_spec(
    workspace: CaseWorkspace,
    *,
    max_candidates: int,
    max_parallel: int,
    worker_host: WorkerHost = "pi-rpc",
    strategy_name: str = "random",
    max_runtime_seconds: int = 180,
    max_turns: int = 6,
) -> dict:
    case = workspace.case
    verifier_module = (
        "agentic_any_search_mcp.benchmarking.verifiers.mcq"
        if case.answer_type == "mcq"
        else "agentic_any_search_mcp.benchmarking.verifiers.numeric"
    )
    return {
        "objective": (
            f"Solve benchmark case {case.task_id}. Read QUESTION.md and write "
            "the final answer to answer.json."
        ),
        "metric_name": "accuracy",
        "metric_direction": "maximize",
        "source_path": str(workspace.source_path),
        "edit_surface": {
            "allow": ["answer.json"],
            "deny": ["QUESTION.md", "README.md"],
            "max_file_changes": 1,
        },
        "budget": {"max_candidates": max_candidates, "max_parallel": max_parallel},
        "process_verifiers": [
            {
                "name": "benchmark_accuracy",
                "role": "ranking_signal",
                "command": [
                    "python",
                    "-m",
                    verifier_module,
                    "--prediction",
                    "answer.json",
                    "--gold-file",
                    str(workspace.verifier_gold_path),
                ],
                "cwd": ".",
                "timeout_seconds": 30,
            }
        ],
        "strategy": {
            "name": strategy_name,
            "worker_mode": "agent-session-pool",
            "worker_host": worker_host,
            "worker_budget": {
                "max_runtime_seconds": max_runtime_seconds,
                "max_turns": max_turns,
                "on_exceed": "interrupt",
            },
        },
        "root_hypotheses": [
            "Use direct reasoning and write only the final answer label or number.",
            "Check the options carefully before writing answer.json.",
        ],
        "constraints": {
            "benchmark": case.benchmark,
            "task_id": case.task_id,
            "answer_type": case.answer_type,
        },
    }


def _render_question(case: BenchmarkCase) -> str:
    lines = [f"# {case.benchmark} {case.task_id}", "", case.question.strip(), ""]
    if case.choices:
        lines.append("Choices:")
        for choice in case.choices:
            lines.append(f"{choice.label}. {choice.text}")
        lines.append("")
    return "\n".join(lines)


def _render_instructions(case: BenchmarkCase) -> str:
    if case.answer_type == "mcq":
        answer_shape = '{"answer": "A"}'
    else:
        answer_shape = '{"answer": "42"}'
    return (
        "Read QUESTION.md. Edit answer.json with the final answer only.\n"
        f"Expected JSON shape: {answer_shape}\n"
    )
