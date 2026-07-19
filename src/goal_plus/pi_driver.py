from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from goal_plus.pi_worker import _workspace_progress_handoff, run_pi_rpc_worker
from goal_plus.runtime import FileSearchRuntime
from goal_plus.tools import SearchTools


def _search_tools_for_pi_rpc_run(root_dir: Path | str, run_id: str) -> SearchTools:
    runtime = FileSearchRuntime(root_dir)
    run = runtime._load_run(run_id)
    frozen = runtime._load_frozen_spec(run.frozen_spec_id)
    worker_host = frozen.spec.strategy.worker_host
    if worker_host != "pi-rpc":
        raise ValueError(
            "Pi search drivers require SearchSpec strategy.worker_host='pi-rpc'; "
            f"got {worker_host!r}. Freeze a Pi SearchSpec before opening a Pi pool."
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


def _failure_details(stage: str, exc: Exception) -> dict[str, str]:
    message = str(exc)
    if len(message) > 500:
        message = message[:500] + "..."
    return {
        "stage": stage,
        "error_type": type(exc).__name__,
        "message": message,
    }


def _failed_candidate_result(
    *,
    run_id: str,
    candidate_id: str,
    agent_session_id: str,
    launch: dict[str, Any],
    steps: list[dict[str, Any]],
    failure: dict[str, str],
    handle: dict[str, Any] | None = None,
    bound_session: dict[str, Any] | None = None,
    handle_bind_failure: dict[str, str] | None = None,
) -> dict[str, Any]:
    result = {
        "ok": False,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "agent_session_id": agent_session_id,
        "launch": launch,
        "handle": handle,
        "bound_session": bound_session,
        "final_score_report": None,
        "steps": steps,
        "failure": failure,
        "error": failure["message"],
    }
    if handle_bind_failure is not None:
        result["handle_bind_failure"] = handle_bind_failure
    return result


def _verifier_infrastructure_report(
    tools: SearchTools,
    run_id: str,
    candidate_id: str,
) -> dict[str, Any] | None:
    record = tools.runtime._load_candidate_record(run_id, candidate_id)
    report = record.score_report
    if report is None:
        return None
    for result in report.verifier_results:
        if (
            result.failure_class == "VerifierWorkspaceSideEffect"
            or result.metrics.get("infrastructure_failure") is True
            or result.metrics.get("candidate_action") == "stop_and_report"
        ):
            return report.model_dump(mode="json")
    return None


def _pi_resume_agent_session_id(
    tools: SearchTools,
    *,
    run_id: str,
    candidate_id: str,
    requested_id: str | None,
) -> str:
    sessions = [
        session
        for session in tools.runtime._load_agent_sessions(run_id)
        if session.candidate_id == candidate_id and session.host == "pi-rpc"
    ]
    if requested_id is not None:
        matching = [
            session for session in sessions if session.agent_session_id == requested_id
        ]
        if not matching:
            raise ValueError(
                f"agent session {requested_id!r} does not belong to Pi candidate "
                f"{candidate_id!r}"
            )
        return requested_id
    if not sessions:
        raise RuntimeError(
            f"Pi candidate {candidate_id!r} has no native session to continue"
        )
    return sessions[-1].agent_session_id


def run_pi_search_candidate(
    *,
    root_dir: Path | str,
    run_id: str,
    candidate_id: str,
    redispatch: bool = False,
    resume_agent_session_id: str | None = None,
    worker_budget: dict[str, Any] | None = None,
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
    worker_budget_override = dict(worker_budget) if worker_budget is not None else None

    if redispatch:
        resumed_session_id = _pi_resume_agent_session_id(
            tools,
            run_id=run_id,
            candidate_id=candidate_id,
            requested_id=resume_agent_session_id,
        )
        session = tools.search_continue_agent_session(
            agent_session_id=resumed_session_id,
            worker_budget=worker_budget_override,
        )
    else:
        if resume_agent_session_id is not None:
            raise ValueError("resume_agent_session_id requires redispatch=true")
        session = tools.search_start_agent_session(
            run_id=run_id,
            candidate_id=candidate_id,
            worker_budget=worker_budget_override,
        )
    agent_session_id = str(session["agent_session_id"])
    launch = dict(session["launch"])
    steps.append(
        {
            "tool": (
                "search_continue_agent_session"
                if redispatch
                else "search_start_agent_session"
            ),
            "agent_session_id": agent_session_id,
            "candidate_id": candidate_id,
            "worker_budget_override": worker_budget_override,
        }
    )

    runner = worker_runner or run_pi_rpc_worker
    try:
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
    except Exception as exc:
        failure = _failure_details("worker_runner", exc)
        progress_handoff = _workspace_progress_handoff(
            Path(str(launch["cwd"])),
            root=Path(str(launch.get("root") or root_dir)),
            run_id=run_id,
            candidate_id=candidate_id,
            timed_out=False,
            runner_failed=True,
            assistant_text=None,
        )
        handle = {
            "host": "pi-rpc",
            "external_id": agent_session_id,
            "metadata": {
                "runner_failed": True,
                "failure_stage": failure["stage"],
                "error_type": failure["error_type"],
                "error": failure["message"],
                "progress_handoff": progress_handoff,
                "timed_out": False,
                "continuation": str(
                    launch.get("continuation") or "native_session"
                ),
            },
        }
        steps.append(
            {
                "tool": "pi_rpc_run_worker",
                "agent_session_id": agent_session_id,
                "status": "failed",
                "error_type": failure["error_type"],
            }
        )
        try:
            bound_session = tools.search_bind_agent_handle(
                agent_session_id=agent_session_id,
                handle=handle,
            )
        except Exception as bind_exc:
            bind_failure = _failure_details("bind_synthetic_failure_handle", bind_exc)
            steps.append(
                {
                    "tool": "search_bind_agent_handle",
                    "agent_session_id": agent_session_id,
                    "status": "failed",
                    "error_type": bind_failure["error_type"],
                }
            )
            return _failed_candidate_result(
                run_id=run_id,
                candidate_id=candidate_id,
                agent_session_id=agent_session_id,
                launch=launch,
                steps=steps,
                failure=failure,
                handle=handle,
                handle_bind_failure=bind_failure,
            )
        steps.append(
            {
                "tool": "search_bind_agent_handle",
                "agent_session_id": agent_session_id,
                "external_id": agent_session_id,
                "status": "failed_handle_bound",
            }
        )
        return _failed_candidate_result(
            run_id=run_id,
            candidate_id=candidate_id,
            agent_session_id=agent_session_id,
            launch=launch,
            steps=steps,
            failure=failure,
            handle=handle,
            bound_session=bound_session,
        )
    steps.append(
        {
            "tool": "pi_rpc_run_worker",
            "agent_session_id": agent_session_id,
            "external_id": handle.get("external_id"),
        }
    )

    try:
        bound_session = tools.search_bind_agent_handle(
            agent_session_id=agent_session_id,
            handle=handle,
        )
    except Exception as exc:
        return _failed_candidate_result(
            run_id=run_id,
            candidate_id=candidate_id,
            agent_session_id=agent_session_id,
            launch=launch,
            steps=steps,
            failure=_failure_details("bind_agent_handle", exc),
            handle=handle,
        )
    steps.append(
        {
            "tool": "search_bind_agent_handle",
            "agent_session_id": agent_session_id,
            "external_id": bound_session.get("host_handle", {}).get("external_id"),
        }
    )

    final_score_report = _verifier_infrastructure_report(
        tools,
        run_id,
        candidate_id,
    )
    infrastructure_failure = final_score_report is not None
    if final_verify and infrastructure_failure:
        steps.append(
            {
                "tool": "search_run_verifier",
                "candidate_id": candidate_id,
                "status": "skipped_duplicate_infrastructure_failure",
                "failure_class": "VerifierWorkspaceSideEffect",
                "candidate_action": "stop_and_report",
            }
        )
    elif final_verify:
        try:
            final_score_report = tools.search_run_verifier(
                run_id=run_id,
                candidate_id=candidate_id,
                scope="process",
                agent_session_id=None,
            )
        except Exception as exc:
            return _failed_candidate_result(
                run_id=run_id,
                candidate_id=candidate_id,
                agent_session_id=agent_session_id,
                launch=launch,
                steps=steps,
                failure=_failure_details("final_verifier", exc),
                handle=handle,
                bound_session=bound_session,
            )
        steps.append(
            {
                "tool": "search_run_verifier",
                "candidate_id": candidate_id,
                "aggregate_score": final_score_report.get("aggregate_score"),
                "process_passed": final_score_report.get("process_passed"),
            }
        )

    result = {
        "ok": not infrastructure_failure,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "agent_session_id": agent_session_id,
        "launch": launch,
        "handle": handle,
        "bound_session": bound_session,
        "final_score_report": final_score_report,
        "steps": steps,
    }
    if infrastructure_failure:
        result.update(
            {
                "infrastructure_failure": True,
                "candidate_action": "stop_and_report",
                "failure": {
                    "stage": "worker_verifier",
                    "error_type": "VerifierWorkspaceSideEffect",
                    "message": (
                        "worker verifier changed the candidate workspace; "
                        "repair and refreeze instead of retrying"
                    ),
                },
                "error": (
                    "worker verifier changed the candidate workspace; "
                    "repair and refreeze instead of retrying"
                ),
            }
        )
    return result
