from __future__ import annotations

from pathlib import Path

from goal_plus.runtime import FileSearchRuntime
from goal_plus.statistics import build_run_statistics

from tests.test_runtime_unit import make_project, spec_for


def _observation(
    *,
    provider: str,
    model: str,
    terminal_state: str,
    timed_out: bool,
    duration: float,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    cost: float,
) -> dict[str, object]:
    processed = input_tokens + cached_tokens + output_tokens
    return {
        "execution": {
            "provider": provider,
            "model": model,
            "terminal_state": terminal_state,
            "timed_out": timed_out,
            "runner_failed": False,
            "duration_seconds": duration,
            "ended_at": None,
            "time_to_first_token_ms": None,
        },
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "cache_write_tokens": 0,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": None,
            "total_tokens": input_tokens + output_tokens,
            "processed_tokens": processed,
            "cost_usd": cost,
            "assistant_messages": 1,
            "tool_calls": 1,
            "tool_results": 1,
        },
    }


def test_run_statistics_split_worker_parent_usage_and_stable_terminal_time(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    frozen = runtime.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    first = runtime.start_agent_session(run_id, task.candidate_id)
    second = runtime.redispatch_candidate(run_id, task.candidate_id)
    task.workspace.joinpath("initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        task.candidate_id,
        agent_session_id=second.agent_session_id,
    )
    runtime.select(run_id)
    runtime.promote(run_id, task.candidate_id)

    run = runtime._load_run(run_id)
    candidates = runtime._load_candidate_records(run_id)
    sessions = runtime._load_agent_sessions(run_id)
    observations = {
        first.agent_session_id: _observation(
            provider="glm-proxy",
            model="GLM-5.2",
            terminal_state="timed_out",
            timed_out=True,
            duration=10.0,
            input_tokens=0,
            cached_tokens=0,
            output_tokens=0,
            cost=0.0,
        ),
        second.agent_session_id: _observation(
            provider="openai-codex",
            model="gpt-5.6-luna",
            terminal_state="completed",
            timed_out=False,
            duration=5.0,
            input_tokens=100,
            cached_tokens=50,
            output_tokens=20,
            cost=0.25,
        ),
    }

    first_snapshot = build_run_statistics(
        run,
        frozen,
        candidates,
        sessions,
        observations,
        baseline_score=-1.0,
        target_score=0.0,
        now_epoch=2_000_000_000.0,
    )
    later_snapshot = build_run_statistics(
        run,
        frozen,
        candidates,
        sessions,
        observations,
        baseline_score=-1.0,
        target_score=0.0,
        now_epoch=2_000_001_000.0,
    )

    assert first_snapshot["scores"]["target_reached"] is True
    assert first_snapshot["scores"]["best_improvement_from_baseline"] == 1.0
    assert first_snapshot["workers"]["productive_sessions"] == 1
    assert first_snapshot["workers"]["successful_sessions"] == 1
    assert first_snapshot["workers"]["timed_out"] == 1
    assert first_snapshot["workers"]["models"] == {
        "GLM-5.2": 1,
        "gpt-5.6-luna": 1,
    }
    assert first_snapshot["lineage"] == {
        "root_candidates": 1,
        "derived_candidates": 0,
        "multi_parent_candidates": 0,
        "lineage_edges": 0,
        "max_depth": 0,
        "cycles_detected": 0,
    }
    assert first_snapshot["selection"]["parent_verified"] is True
    assert first_snapshot["selection"]["survived"] is True
    assert first_snapshot["timing"]["time_to_first_improvement_seconds"] is not None
    assert first_snapshot["timing"]["time_to_threshold_seconds"] is not None
    assert first_snapshot["verifiers"]["process_runs"] == 2
    assert first_snapshot["verifiers"]["worker_process_runs"] == 1
    assert first_snapshot["verifiers"]["parent_process_runs"] == 1
    assert first_snapshot["verifiers"]["process_verifier_results"] == 2
    assert first_snapshot["usage"]["processed_tokens"] == 170
    assert first_snapshot["usage"]["cost_usd"] == 0.25
    assert first_snapshot["run"]["age_seconds"] != later_snapshot["run"]["age_seconds"]
    assert (
        first_snapshot["run"]["observed_duration_seconds"]
        == later_snapshot["run"]["observed_duration_seconds"]
    )
