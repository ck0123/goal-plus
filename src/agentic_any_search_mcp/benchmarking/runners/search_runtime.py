from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from ...models import SearchSpec
from ...pi_worker import run_pi_rpc_worker
from ...runtime import FileSearchRuntime
from ..adapters.search_spec import build_search_spec, prepare_case_workspace
from ..cases import BenchmarkCase
from ..paper_compat import paper_fields
from ..scoring import ScoreResult


WorkerBackend = Literal["fixed", "pi-rpc"]


def run_search_case(
    case: BenchmarkCase,
    *,
    root_dir: Path,
    worker_backend: WorkerBackend = "fixed",
    fixed_answer: str | None = None,
    max_candidates: int = 1,
    max_parallel: int = 1,
    worker_host: str = "pi-rpc",
    strategy_name: str = "random",
    max_runtime_seconds: int = 180,
    max_turns: int = 6,
    pi_binary: str = "pi",
    pi_provider: str | None = None,
    pi_model_id: str | None = None,
    pi_thinking: str | None = None,
) -> dict[str, Any]:
    root_dir.mkdir(parents=True, exist_ok=True)
    workspace = prepare_case_workspace(case, root_dir / "workspaces")
    spec_dict = build_search_spec(
        workspace,
        max_candidates=max_candidates,
        max_parallel=max_parallel,
        worker_host=worker_host,  # type: ignore[arg-type]
        strategy_name=strategy_name,
        max_runtime_seconds=max_runtime_seconds,
        max_turns=max_turns,
    )
    runtime = FileSearchRuntime(root_dir / ".search")
    frozen = runtime.freeze_spec(SearchSpec.model_validate(spec_dict), [])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=max_candidates)
    tasks = runtime.start_batch(run_id, plan.plan_id)

    handles: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for task in tasks:
        if worker_backend == "fixed":
            if fixed_answer is None:
                raise ValueError("fixed worker backend requires fixed_answer")
            _write_answer(Path(task.workspace), fixed_answer)
        elif worker_backend == "pi-rpc":
            session = runtime.start_agent_session(
                run_id,
                task.candidate_id,
                {
                    "goal": (
                        "Read QUESTION.md, solve the benchmark item, edit answer.json "
                        "with only the final answer, then call search_run_verifier."
                    )
                },
            )
            handle = run_pi_rpc_worker(
                session.launch,
                pi_binary=pi_binary,
                provider=pi_provider,
                model_id=pi_model_id,
                thinking_level=pi_thinking,
            )
            runtime.bind_agent_handle(session.agent_session_id, handle)
            handles.append(handle)
        else:
            raise ValueError(f"unsupported worker backend: {worker_backend}")

        report = runtime.run_verifier(run_id, task.candidate_id)
        reports.append(report.model_dump(mode="json"))

    selected = runtime.select(run_id)
    selected_candidate_id = str(selected["selected_candidate_id"])
    selected_report = next(
        report for report in reports if report["candidate_id"] == selected_candidate_id
    )
    metrics = _first_metrics(selected_report)
    scored = ScoreResult(
        prediction=str(metrics.get("prediction", "")),
        gold=str(metrics.get("gold", case.gold)),
        correct=bool(metrics.get("correct", False)),
        score=float(selected_report.get("aggregate_score") or 0.0),
        parser_error=metrics.get("parser_error"),
    )
    paper_result = {
        "benchmark": case.benchmark,
        "task_id": case.task_id,
        "question": case.question,
        "choices": [choice.__dict__ for choice in case.choices],
        "gold": scored.gold,
        "prediction": scored.prediction,
        "correct": scored.correct,
        "score": scored.score,
        "mode": "mcp_search",
        "n_calls": len(tasks),
        "parser_error": scored.parser_error,
    } | paper_fields(case, scored)
    return {
        "paper_result": paper_result,
        "search_diagnostics": {
            "run_id": run_id,
            "candidate_count": len(tasks),
            "best_candidate_id": selected_candidate_id,
            "pass_at_k": any((report.get("aggregate_score") or 0.0) > 0 for report in reports),
            "selected_correct": paper_result["correct"],
            "verifier_score": paper_result["score"],
            "strategy": strategy_name,
            "worker_backend": worker_backend,
            "worker_host": worker_host,
            "handles": handles,
        },
        "candidate_reports": reports,
        "workspace": str(workspace.source_path),
    }


def _write_answer(workspace: Path, answer: str) -> None:
    (workspace / "answer.json").write_text(
        json.dumps({"answer": answer}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _first_metrics(report: dict[str, Any]) -> dict[str, Any]:
    for result in report.get("verifier_results") or []:
        metrics = result.get("metrics") or {}
        if isinstance(metrics, dict):
            return metrics
    return {}
