from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from agentic_any_search_mcp.pi_worker import run_pi_rpc_worker
from agentic_any_search_mcp.runtime import FileSearchRuntime
from agentic_any_search_mcp.tools import SearchTools


def _search_tools_for_pi_rpc_run(root_dir: Path | str, run_id: str) -> SearchTools:
    runtime = FileSearchRuntime(root_dir)
    run = runtime._load_run(run_id)
    frozen = runtime._load_frozen_spec(run.frozen_spec_id)
    worker_host = frozen.spec.strategy.worker_host
    if worker_host != "pi-rpc":
        raise ValueError(
            "Pi search drivers require SearchSpec strategy.worker_host='pi-rpc'; "
            f"got {worker_host!r}. Freeze a Pi SearchSpec before calling "
            "pi_search_run_candidate or pi_search_run_batch."
        )
    return SearchTools(runtime)


def _worker_kwargs(
    *,
    pi_binary: str,
    extension_path: Path | str | None,
    thinking_level: str | None,
    model_pattern: str | None,
    provider: str | None,
    model_id: str | None,
) -> dict[str, Any]:
    return {
        "pi_binary": pi_binary,
        "extension_path": extension_path,
        "thinking_level": thinking_level,
        "model_pattern": model_pattern,
        "provider": provider,
        "model_id": model_id,
    }


def run_pi_search_candidate(
    *,
    root_dir: Path | str,
    run_id: str,
    candidate_id: str,
    directive: dict[str, Any] | str | None = None,
    final_verify: bool = True,
    worker_runner: Callable[..., dict[str, Any]] | None = None,
    pi_binary: str = "pi",
    extension_path: Path | str | None = None,
    thinking_level: str | None = None,
    model_pattern: str | None = None,
    provider: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    tools = _search_tools_for_pi_rpc_run(root_dir, run_id)
    steps: list[dict[str, Any]] = []

    session = tools.search_start_agent_session(
        run_id=run_id,
        candidate_id=candidate_id,
        directive=directive,
    )
    agent_session_id = str(session["agent_session_id"])
    launch = dict(session["launch"])
    steps.append(
        {
            "tool": "search_start_agent_session",
            "agent_session_id": agent_session_id,
            "candidate_id": candidate_id,
        }
    )

    runner = worker_runner or run_pi_rpc_worker
    handle = runner(
        launch,
        **_worker_kwargs(
            pi_binary=pi_binary,
            extension_path=extension_path,
            thinking_level=thinking_level,
            model_pattern=model_pattern,
            provider=provider,
            model_id=model_id,
        ),
    )
    steps.append(
        {
            "tool": "pi_rpc_run_worker",
            "agent_session_id": agent_session_id,
            "external_id": handle.get("external_id"),
        }
    )

    bound_session = tools.search_bind_agent_handle(
        agent_session_id=agent_session_id,
        handle=handle,
    )
    steps.append(
        {
            "tool": "search_bind_agent_handle",
            "agent_session_id": agent_session_id,
            "external_id": bound_session.get("host_handle", {}).get("external_id"),
        }
    )

    final_score_report: dict[str, Any] | None = None
    if final_verify:
        final_score_report = tools.search_run_verifier(
            run_id=run_id,
            candidate_id=candidate_id,
            scope="process",
            agent_session_id=None,
        )
        steps.append(
            {
                "tool": "search_run_verifier",
                "candidate_id": candidate_id,
                "aggregate_score": final_score_report.get("aggregate_score"),
                "process_passed": final_score_report.get("process_passed"),
            }
        )

    return {
        "ok": True,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "agent_session_id": agent_session_id,
        "launch": launch,
        "handle": handle,
        "bound_session": bound_session,
        "final_score_report": final_score_report,
        "steps": steps,
    }


def run_pi_search_batch(
    *,
    root_dir: Path | str,
    run_id: str,
    candidate_ids: list[str],
    directive: dict[str, Any] | str | None = None,
    final_verify: bool = True,
    max_parallel: int | None = None,
    worker_runner: Callable[..., dict[str, Any]] | None = None,
    pi_binary: str = "pi",
    extension_path: Path | str | None = None,
    thinking_level: str | None = None,
    model_pattern: str | None = None,
    provider: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    if not candidate_ids:
        raise ValueError("candidate_ids must not be empty")
    _search_tools_for_pi_rpc_run(root_dir, run_id)
    worker_count = max_parallel or len(candidate_ids)
    if worker_count <= 0:
        raise ValueError("max_parallel must be > 0")
    worker_count = min(worker_count, len(candidate_ids))

    def run_one(candidate_id: str) -> dict[str, Any]:
        return run_pi_search_candidate(
            root_dir=root_dir,
            run_id=run_id,
            candidate_id=candidate_id,
            directive=directive,
            final_verify=final_verify,
            worker_runner=worker_runner,
            pi_binary=pi_binary,
            extension_path=extension_path,
            thinking_level=thinking_level,
            model_pattern=model_pattern,
            provider=provider,
            model_id=model_id,
        )

    results_by_candidate: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(run_one, candidate_id): candidate_id
            for candidate_id in candidate_ids
        }
        for future in as_completed(futures):
            candidate_id = futures[future]
            try:
                results_by_candidate[candidate_id] = future.result()
            except Exception as exc:
                results_by_candidate[candidate_id] = {
                    "ok": False,
                    "run_id": run_id,
                    "candidate_id": candidate_id,
                    "error": str(exc),
                }

    ordered_results = [results_by_candidate[candidate_id] for candidate_id in candidate_ids]
    return {
        "ok": all(result.get("ok") is True for result in ordered_results),
        "run_id": run_id,
        "candidate_ids": candidate_ids,
        "max_parallel": worker_count,
        "results": ordered_results,
    }
