from __future__ import annotations

from pathlib import Path

import pytest

from goal_plus.goal_plus import FileGoalPlusRuntime
from goal_plus.models import GoalPlusRecord, IterationRecord, SearchSpec
from goal_plus.reporting import (
    _build_timeline,
    _epoch,
    _metric_readout,
    _render_search_trajectory,
    _render_sessions,
    _render_statistics,
    _render_timeline,
    _search_trajectory_payload,
    _task_details,
    build_html_report_data,
    render_html_report,
)
from goal_plus.runtime import FileSearchRuntime

from tests._runtime_helpers import make_project, spec_for


def test_search_report_generates_self_contained_html_with_multi_search_timeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "goal_plus.reporting._load_plotly_javascript",
        lambda: "window.Plotly={newPlot:function(){},Plots:{resize:function(){}}};",
    )
    project = make_project(tmp_path)
    root = tmp_path / ".search"
    search = FileSearchRuntime(root)
    frozen = search.freeze_spec(spec_for(project), [project / "evaluator.py"])

    first_run = search.create_run(frozen.frozen_spec_id)
    search.plan_next(first_run, requested_k=1)

    second_run = search.create_run(frozen.frozen_spec_id)
    second_plan = search.plan_next(second_run, requested_k=1)
    [candidate] = search.start_batch(second_run, second_plan.plan_id)
    session = search.start_agent_session(second_run, candidate.candidate_id)
    search.run_verifier(
        second_run,
        candidate.candidate_id,
        agent_session_id=session.agent_session_id,
        hypothesis="exercise the durable timeline",
    )

    goals = FileGoalPlusRuntime(root)
    goal = goals.create_goal("Optimize <script>alert('unsafe')</script> safely")
    goals.link_search_run(goal.goal_plus_id, frozen.frozen_spec_id, first_run)
    goals.link_search_run(goal.goal_plus_id, frozen.frozen_spec_id, second_run)

    with pytest.raises(RuntimeError, match="before every linked Goal Plus record"):
        search.report(second_run)
    assert not (root / "runs" / second_run / "report.md").exists()
    assert not (root / "runs" / second_run / "report.html").exists()

    goals.set_status(
        goal.goal_plus_id,
        "complete",
        reason="synthetic reporting fixture is ready",
        evidence=[{"kind": "unit_test"}],
    )
    markdown_path = search.report(second_run)
    html_path = markdown_path.with_suffix(".html")
    html = html_path.read_text(encoding="utf-8")

    assert markdown_path.is_file()
    assert html_path.is_file()
    assert 'data-report-schema="goal-plus-report/v1"' in html
    assert first_run in html
    assert second_run in html
    assert "Search Task 01" in html
    assert "Search Task 02" in html
    assert "Orchestration" in html
    assert "parallel_loops" in html
    assert session.agent_session_id in html
    assert html.count("<h2>Search Execution Timeline</h2>") == 2
    assert "Goal Plus Summary" in html
    assert "Goal status" in html
    assert "Selected score" in html
    assert "No score threshold was configured" in html
    assert "Metric availability" in html
    assert "Goal Plus Lifecycle" not in html
    assert "Planning Rounds" not in html
    assert "Final GP Aggregate" not in html
    assert "Unavailable Metrics Audit" not in html
    assert "Verifier activity" in html
    assert "Complete normalized report data" in html
    assert "Complete Search Trajectory" in html
    assert "data-search-trajectory=" in html
    assert "window.Plotly={newPlot" in html
    assert "payload.call_window" in html
    assert "node.innerHTML = '';\n      var rendering = window.Plotly.newPlot" in html
    assert "Not eligible · not scored" in html
    assert "linear score axis" in html
    assert 'class="score-step"' not in html
    assert "&lt;script&gt;alert(&#x27;unsafe&#x27;)&lt;/script&gt;" in html
    assert "<script>alert('unsafe')</script>" not in html
    assert "<script src=" not in html

    monkeypatch.setattr("goal_plus.reporting._load_plotly_javascript", lambda: None)
    fallback_html = render_html_report(build_html_report_data(root, second_run))
    assert "data-search-trajectory=" in fallback_html
    assert "data-trajectory-fallback" in fallback_html
    assert "Complete Search Trajectory" in fallback_html
    assert 'class="score-step"' not in fallback_html


def test_search_trajectory_payload_keeps_parallel_candidate_loops() -> None:
    task = {
        "frozen_spec": {"metric_name": "quality", "metric_direction": "maximize"},
        "statistics": {
            "scores": {
                "metric_name": "quality",
                "direction": "maximize",
                "baseline": 0.0,
                "selected": 3.0,
            }
        },
        "candidates": [
            {
                "candidate_id": "c001",
                "selected": False,
                "iterations": [
                    {
                        "iteration": 1,
                        "score": 1.0,
                        "process_passed": True,
                        "created_at": "2026-01-01T00:00:01Z",
                    },
                    {
                        "iteration": 2,
                        "score": 2.0,
                        "process_passed": True,
                        "created_at": "2026-01-01T00:00:03Z",
                    },
                ],
            },
            {
                "candidate_id": "c002",
                "selected": True,
                "iterations": [
                    {
                        "iteration": 1,
                        "score": 0.5,
                        "process_passed": True,
                        "created_at": "2026-01-01T00:00:02Z",
                    },
                    {
                        "iteration": 2,
                        "agent_session_id": "agent_002",
                        "score": 3.0,
                        "process_passed": True,
                        "created_at": "2026-01-01T00:00:04Z",
                    },
                    {
                        "iteration": 3,
                        "agent_session_id": "agent_002",
                        "score": 3.0,
                        "process_passed": True,
                        "created_at": "2026-01-01T00:00:05Z",
                    },
                ],
            },
        ],
    }

    payload = _search_trajectory_payload(task)

    assert payload is not None
    assert payload["evaluations"] == 5
    assert payload["passing_evaluations"] == 5
    assert payload["ineligible_evaluations"] == 0
    assert payload["failed_evaluations"] == 0
    assert payload["unknown_evaluations"] == 0
    assert payload["call_window"] == {
        "start": 0,
        "end": 5,
        "tick": 1,
        "marker_size": 7,
    }
    assert payload["score_axis"]["type"] == "linear"
    assert [trace["calls"] for trace in payload["trajectories"]] == [[1, 3], [2, 4, 5]]
    assert payload["global_best"] == {
        "calls": [0, 1, 2, 3, 4, 5],
        "scores": [0.0, 1.0, 1.0, 2.0, 3.0, 3.0],
    }
    assert payload["selected_point"] == {
        "candidate_id": "c002",
        "call": 5,
        "score": 3.0,
    }
    assert payload["trajectories"][1]["details"][1][1] == "worker verifier"


def test_search_trajectory_payload_adapts_axes_and_excludes_failed_scores() -> None:
    iterations = [
        {
            "iteration": iteration,
            "score": 0.0 if iteration == 1 else 1000.0 / (iteration + 1),
            "process_passed": iteration != 1,
        }
        for iteration in range(1, 131)
    ]
    task = {
        "frozen_spec": {"metric_name": "cycles", "metric_direction": "minimize"},
        "statistics": {
            "scores": {
                "metric_name": "cycles",
                "direction": "minimize",
                "baseline": 1000.0,
                "selected": 1000.0 / 131,
            }
        },
        "candidates": [
            {
                "candidate_id": "c001",
                "selected": True,
                "iterations": iterations,
            }
        ],
    }

    payload = _search_trajectory_payload(task)

    assert payload is not None
    assert payload["evaluations"] == 130
    assert payload["passing_evaluations"] == 129
    assert payload["ineligible_evaluations"] == 1
    assert payload["failed_evaluations"] == 1
    assert payload["unknown_evaluations"] == 0
    assert payload["call_window"] == {
        "start": 0,
        "end": 130,
        "tick": 20,
        "marker_size": 5,
    }
    assert payload["score_axis"]["type"] == "log"
    assert payload["trajectories"][0]["calls"][0] == 2
    assert payload["trajectories"][0]["failed_calls"] == [1]
    assert payload["global_best"]["scores"][:2] == [1000.0, 1000.0]
    assert min(payload["global_best"]["scores"]) > 0
    assert payload["selected_point"] == {
        "candidate_id": "c001",
        "call": 130,
        "score": 1000.0 / 131,
    }
    fallback = _render_search_trajectory(payload)
    assert "data-trajectory-fallback" in fallback
    assert 'class="trajectory-candidate trajectory-series-0"' in fallback
    assert 'class="trajectory-global"' in fallback
    assert 'class="trajectory-baseline"' in fallback
    assert 'class="trajectory-failure"' in fallback


def test_failed_and_unknown_iterations_never_enter_report_best_paths(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    root = tmp_path / ".search"
    search = FileSearchRuntime(root)
    frozen = search.freeze_spec(
        spec_for(project, max_candidates=1, direction="minimize"),
        [project / "evaluator.py"],
    )
    run_id = search.create_run(frozen.frozen_spec_id)
    plan = search.plan_next(run_id, requested_k=1)
    candidate = search.start_batch(run_id, plan.plan_id)[0]
    record = search._load_candidate_record(run_id, candidate.candidate_id)
    record.iterations = [
        IterationRecord(
            iteration=1,
            agent_session_id="agent_score",
            score=1.0,
            process_passed=False,
            failure_class="correctness",
            created_at="2026-01-01T00:01:00Z",
        ),
        IterationRecord(
            iteration=2,
            agent_session_id="agent_score",
            score=90.0,
            process_passed=True,
            created_at="2026-01-01T00:02:00Z",
        ),
        IterationRecord(
            iteration=3,
            agent_session_id="agent_score",
            score=0.5,
            process_passed=None,
            created_at="2026-01-01T00:03:00Z",
        ),
    ]
    search._write_candidate_record(run_id, record)
    task = _task_details(
        root,
        {
            "run_id": run_id,
            "statistics": {
                "scores": {
                    "metric_name": "combined_score",
                    "direction": "minimize",
                    "baseline": 100.0,
                    "selected": None,
                }
            },
        },
        run_id,
    )

    assert task["candidates"][0]["best_iteration"] == 2
    assert task["candidates"][0]["best_score"] == 90.0

    task["sessions"] = [
        {
            "agent_session_id": "agent_score",
            "candidate_id": candidate.candidate_id,
            "started_at": "2026-01-01T00:00:30Z",
            "ended_at": "2026-01-01T00:03:30Z",
            "duration_seconds": 180.0,
            "terminal_state": "completed",
            "processed_tokens": 100,
            "verifier_runs": 3,
        }
    ]
    _build_timeline(None, [], [task])
    [worker] = [
        event
        for event in task["timeline"]["events"]
        if event["kind"] == "worker_session"
    ]
    assert worker["score"] == 90.0
    assert [
        point["score"]
        for point in task["timeline"]["performance"]["score"]["points"]
    ] == [90.0]

    payload = _search_trajectory_payload(task)
    assert payload is not None
    assert payload["passing_evaluations"] == 1
    assert payload["failed_evaluations"] == 1
    assert payload["unknown_evaluations"] == 1
    assert payload["trajectories"][0]["scores"] == [90.0]
    assert payload["trajectories"][0]["failed_scores"] == [1.0, 0.5]
    assert payload["global_best"] == {
        "calls": [0, 1, 2, 3],
        "scores": [100.0, 100.0, 90.0, 90.0],
    }


def test_session_observability_is_preserved_and_rendered_completely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    root = tmp_path / ".search"
    search = FileSearchRuntime(root)
    frozen = search.freeze_spec(spec_for(project, max_candidates=1), [project / "evaluator.py"])
    run_id = search.create_run(frozen.frozen_spec_id)
    plan = search.plan_next(run_id, requested_k=1)
    candidate = search.start_batch(run_id, plan.plan_id)[0]
    session = search.start_agent_session(run_id, candidate.candidate_id)
    observation = {
        "schema_version": 2,
        "agent_session_id": session.agent_session_id,
        "run_id": run_id,
        "candidate_id": candidate.candidate_id,
        "host": "codex",
        "source": "codex_session_jsonl",
        "identity": {
            "native_session_id": "native-thread-1",
            "external_id": "external-1",
            "task_name": "search_candidate_1",
            "nickname": "worker-1",
        },
        "execution": {
            "provider": "openai-codex",
            "model": "gpt-test",
            "reasoning_effort": "high",
            "service_tier": "priority",
            "started_at": "2026-01-01T00:00:00Z",
            "ended_at": "2026-01-01T00:01:00Z",
            "duration_seconds": 55.0,
            "wall_duration_seconds": 60.0,
            "time_to_first_token_ms": 250.0,
            "turns_completed": 3,
            "terminal_state": "completed",
            "timed_out": False,
            "runner_failed": False,
            "exit_code": 0,
        },
        "usage": {
            "scope": "session_total",
            "input_tokens": 1000,
            "cached_input_tokens": 600,
            "cache_write_tokens": None,
            "output_tokens": 200,
            "reasoning_output_tokens": 50,
            "total_tokens": 1200,
            "processed_tokens": 1200,
            "cost_usd": 0.25,
            "assistant_messages": 4,
            "tool_calls": 12,
            "tool_results": 11,
        },
        "context": {
            "tokens": 32000,
            "context_window": 128000,
            "percent": 25.0,
            "source": "codex_last_token_usage",
        },
        "artifacts": {
            "event_log": None,
            "text_log": None,
            "session_file": "/tmp/native-session.jsonl",
        },
        "handoff": {
            "present": True,
            "source_path": ".tmp/handoff.json",
            "error": None,
        },
        "errors": ["collector <warning>"],
    }
    monkeypatch.setattr("goal_plus.reporting._collect_observability", lambda _session: observation)

    task = _task_details(root, {"run_id": run_id}, run_id)
    [rendered_session] = task["sessions"]
    assert rendered_session["observability"] == observation
    assert rendered_session["identity"]["native_session_id"] == "native-thread-1"
    assert rendered_session["execution"]["reasoning_effort"] == "high"
    assert rendered_session["usage"]["tool_results"] == 11
    assert rendered_session["context"]["context_window"] == 128000
    assert rendered_session["artifacts"]["session_file"] == "/tmp/native-session.jsonl"
    assert rendered_session["handoff"]["present"] is True

    html = _render_sessions(task)
    assert "native-thread-1" in html
    assert "Reasoning Effort" in html
    assert "128,000" in html
    assert "12" in html
    assert "/tmp/native-session.jsonl" in html
    assert ".tmp/handoff.json" in html
    assert "collector &lt;warning&gt;" in html
    assert "collector <warning>" not in html


def test_html_report_data_keeps_search_tasks_and_rounds_separate(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    root = tmp_path / ".search"
    search = FileSearchRuntime(root)
    frozen = search.freeze_spec(spec_for(project), [project / "evaluator.py"])
    runs = [search.create_run(frozen.frozen_spec_id) for _ in range(2)]
    for run_id in runs:
        search.plan_next(run_id, requested_k=1)

    goals = FileGoalPlusRuntime(root)
    goal = goals.create_goal("Compare two independent Search tasks")
    for run_id in runs:
        goals.link_search_run(goal.goal_plus_id, frozen.frozen_spec_id, run_id)

    data = build_html_report_data(root, runs[-1])

    assert data["goal_plus_id"] == goal.goal_plus_id
    assert [task["run_id"] for task in data["search_tasks"]] == runs
    assert all(
        task["strategy"]["orchestration_mode"] == "parallel_loops"
        for task in data["search_tasks"]
    )
    assert [len(task["plans"]) for task in data["search_tasks"]] == [1, 1]
    assert all(task["timeline"]["duration_seconds"] for task in data["search_tasks"])
    assert data["snapshot"]["search_task_aggregate"]["search_tasks_total"] == 2
    assert data["snapshot"]["search_task_aggregate"]["planning_rounds_total"] == 2


def test_pi_native_session_resume_renders_distinct_process_dispatches(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    root = tmp_path / ".search"
    search = FileSearchRuntime(root)
    spec_data = spec_for(project, max_candidates=1).model_dump(mode="json")
    spec_data["strategy"] = {
        "name": "random",
        "worker_host": "pi-rpc",
        "worker_budget": {
            "max_runtime_seconds": 60,
            "on_exceed": "interrupt",
        },
    }
    frozen = search.freeze_spec(
        SearchSpec.model_validate(spec_data),
        [project / "evaluator.py"],
    )
    run_id = search.create_run(frozen.frozen_spec_id)
    plan = search.plan_next(run_id, requested_k=1)
    candidate = search.start_batch(run_id, plan.plan_id)[0]
    session = search.start_agent_session(run_id, candidate.candidate_id)

    def bind_dispatch(
        *,
        pid: int,
        start_at: str,
        end_at: str,
        last_entry_id: str,
        entry_count: int,
        cumulative_input: int,
    ) -> None:
        search.bind_agent_handle(
            session.agent_session_id,
            {
                "host": "pi-rpc",
                "external_id": session.agent_session_id,
                "metadata": {
                    "process_pid": pid,
                    "continuation": "native_session",
                    "pi_metrics": {
                        "scope": "session_cumulative_incremental",
                        "dispatch_started_at": start_at,
                        "dispatch_ended_at": end_at,
                        "dispatch_duration_seconds": 10.0,
                        "started_at": "2026-07-19T00:00:00Z",
                        "ended_at": end_at,
                        "duration_seconds": entry_count * 10.0,
                        "final_last_entry_id": last_entry_id,
                        "final_entry_count": entry_count,
                        "usage_delta": {
                            "assistantMessages": 1,
                            "input": 10,
                            "output": 2,
                            "cacheRead": 3,
                            "cacheWrite": 0,
                            "costTotal": 0.01,
                        },
                        "usage_total": {
                            "assistantMessages": entry_count,
                            "input": cumulative_input,
                            "output": entry_count * 2,
                            "cacheRead": entry_count * 3,
                            "cacheWrite": 0,
                            "costTotal": entry_count * 0.01,
                        },
                    },
                },
            },
        )

    bind_dispatch(
        pid=111,
        start_at="2026-07-19T00:00:00Z",
        end_at="2026-07-19T00:00:10Z",
        last_entry_id="entry_1",
        entry_count=1,
        cumulative_input=10,
    )
    continued = search.continue_agent_session(session.agent_session_id)
    assert continued.launch["metrics_baseline"]["last_entry_id"] == "entry_1"
    bind_dispatch(
        pid=222,
        start_at="2026-07-19T00:00:20Z",
        end_at="2026-07-19T00:00:30Z",
        last_entry_id="entry_2",
        entry_count=2,
        cumulative_input=20,
    )
    record = search._load_candidate_record(run_id, candidate.candidate_id)
    record.iterations = [
        IterationRecord(
            iteration=1,
            score=0.0,
            process_passed=True,
            hypothesis="parent baseline",
            summary="parent baseline",
            created_at="2026-07-19T00:00:10Z",
        ),
        IterationRecord(
            iteration=2,
            agent_session_id=session.agent_session_id,
            score=9.0,
            process_passed=True,
            hypothesis="worker improvement",
            summary="worker improvement",
            created_at="2026-07-19T00:00:25Z",
        ),
    ]
    search._write_candidate_record(run_id, record)

    data = build_html_report_data(root, run_id)
    task = data["search_tasks"][0]
    assert len(task["sessions"]) == 2
    assert [item["dispatch_index"] for item in task["sessions"]] == [1, 2]
    assert {item["agent_session_id"] for item in task["sessions"]} == {
        session.agent_session_id
    }
    assert [item["score"] for item in task["sessions"]] == [0.0, 9.0]
    worker_events = [
        event
        for event in task["timeline"]["events"]
        if event["kind"] == "worker_session"
    ]
    assert [event["process_pid"] for event in worker_events] == [111, 222]
    assert [event["attempt_index"] for event in worker_events] == [1, 2]
    assert all(event["attempt_count"] == 2 for event in worker_events)
    assert [event["score"] for event in worker_events] == [0.0, 9.0]
    assert [event["score_raw"] for event in worker_events] == [0.0, 9.0]
    assert [event["score_gain"] for event in worker_events] == [None, None]
    performance = task["timeline"]["performance"]
    assert performance["score"]["baseline"] is None
    assert performance["score"]["baseline_source"] is None
    assert "score_gain" not in performance["metric_ranges"]
    assert performance["metric_ranges"]["score_raw"] == {
        "min": 0.0,
        "max": 9.0,
        "observed": 2,
    }
    html = _render_timeline(task["timeline"], title="Pi dispatch score gain")
    assert "No baseline" in html
    assert 'data-metric-mode="score-raw"' in html
    assert "Score gain</button><button" in html
    assert 'disabled title="Baseline was not observed">Score gain</button>' in html
    assert ">Score raw</button>" in html
    assert 'data-score-gain-baseline="false"' in html
    assert "data-metric-score-gain=" not in html
    assert 'data-metric-score-raw="0.000000000"' in html
    assert 'data-metric-score-raw="9.000000000"' in html


def test_worker_duration_uses_search_scale_not_goal_record_lifecycle() -> None:
    goal = GoalPlusRecord(
        goal_plus_id="gp_0001",
        raw_goal="Keep worker execution separate from record activity",
        status="complete",
        phase="final_audit",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T01:00:00Z",
    )
    tasks = [
        {
            "run_id": "run_0001",
            "run": {"created_at": "2026-01-01T00:01:00Z"},
            "plans": [],
            "sessions": [
                {
                    "agent_session_id": "agent_0002",
                    "started_at": "2026-01-01T00:02:00Z",
                    "ended_at": "2026-01-01T00:03:00Z",
                    "duration_seconds": 60.0,
                    "terminal_state": "completed",
                }
            ],
            "candidates": [
                {
                    "candidate_id": "c001",
                    "iterations": [],
                    "promotion_passed": True,
                    "promotion_evidence_at": "2026-01-01T00:06:29Z",
                }
            ],
        }
    ]

    goal_timeline = _build_timeline(goal, [], tasks)
    search_timeline = tasks[0]["timeline"]
    [worker] = [
        event
        for event in search_timeline["events"]
        if event["kind"] == "worker_session"
    ]

    assert goal_timeline["duration_seconds"] == 3600.0
    assert search_timeline["duration_seconds"] == 329.0
    assert _epoch(worker["end_at"]) - _epoch(worker["start_at"]) == 60.0
    assert goal_timeline["events"][0]["label"] == "Goal record activity window"


def test_metric_lens_combines_score_progression_and_session_efficiency() -> None:
    tasks = [
        {
            "run_id": "run_metric_lens",
            "run": {"created_at": "2026-01-01T00:00:00Z"},
            "frozen_spec": {
                "metric_name": "quality",
                "metric_direction": "maximize",
                "budget": {"max_parallel": 2},
            },
            "statistics": {
                "scores": {
                    "metric_name": "quality",
                    "direction": "maximize",
                    "baseline": 0.5,
                    "selected": 0.8,
                }
            },
            "plans": [],
            "sessions": [
                {
                    "agent_session_id": "agent_metric_001",
                    "candidate_id": "c001",
                    "started_at": "2026-01-01T00:01:00Z",
                    "ended_at": "2026-01-01T00:02:00Z",
                    "duration_seconds": 60.0,
                    "terminal_state": "completed",
                    "processed_tokens": 600,
                    "cost_usd": 0.06,
                    "verifier_runs": 2,
                },
                {
                    "agent_session_id": "agent_metric_002",
                    "candidate_id": "c001",
                    "started_at": "2026-01-01T00:10:00Z",
                    "ended_at": "2026-01-01T00:11:00Z",
                    "duration_seconds": 60.0,
                    "terminal_state": "timed_out",
                    "processed_tokens": 1200,
                    "cost_usd": 0.12,
                    "verifier_runs": 4,
                },
            ],
            "candidates": [
                {
                    "candidate_id": "c001",
                    "iterations": [
                        {
                            "iteration": 1,
                            "agent_session_id": "agent_metric_001",
                            "score": 0.6,
                            "process_passed": True,
                            "created_at": "2026-01-01T00:01:30Z",
                        },
                        {
                            "iteration": 2,
                            "agent_session_id": "agent_metric_002",
                            "score": 0.8,
                            "process_passed": True,
                            "created_at": "2026-01-01T00:10:30Z",
                        },
                    ],
                    "promotion_passed": True,
                    "promotion_evidence_at": "2026-01-01T00:12:00Z",
                }
            ],
        }
    ]

    _build_timeline(None, [], tasks)
    timeline = tasks[0]["timeline"]
    workers = [event for event in timeline["events"] if event["kind"] == "worker_session"]
    performance = timeline["performance"]

    assert [event["tokens_per_minute"] for event in workers] == [600.0, 1200.0]
    assert [event["attempt_index"] for event in workers] == [1, 2]
    assert all(event["attempt_count"] == 2 for event in workers)
    assert [point["score"] for point in performance["score"]["points"]] == [0.6, 0.8]
    assert performance["metric_ranges"]["tokens_per_minute"] == {
        "min": 600.0,
        "max": 1200.0,
        "observed": 2,
    }
    assert performance["metric_ranges"]["score_gain"] == {
        "min": 0.09999999999999998,
        "max": 0.30000000000000004,
        "observed": 2,
    }
    assert performance["metric_ranges"]["score_raw"] == {
        "min": 0.6,
        "max": 0.8,
        "observed": 2,
    }
    assert performance["idle_intervals"][0]["duration_seconds"] == 480.0

    html = _render_timeline(timeline, title="Metric Lens Timeline")

    assert "data-metric-lens" in html
    assert 'data-metric-mode="score-gain"' in html
    assert 'data-metric-score-gain="0.100000000"' in html
    assert 'data-metric-score-raw="0.600000000"' in html
    assert 'data-score-gain-baseline="true"' in html
    assert "Score gain</button><button" in html
    assert ">Score raw</button>" in html
    assert 'data-metric-tokens-per-minute="600.000000000"' in html
    assert 'data-metric-verifier-density="2.000000000" style=' in html
    assert 'class="score-step"' in html
    assert 'r="3"><title>' in html
    assert "Baseline 0.5" in html
    assert "Selected 0.8" in html
    assert "retry 2/2" in html
    assert "session-failure" in html
    assert "Idle 8m 0s" in html


def test_statistics_formats_observed_session_count_as_count() -> None:
    html = _render_statistics(
        {
            "statistics": {
                "timing": {
                    "worker_duration_seconds_total": 2.0,
                    "worker_duration_sessions_observed": 2,
                }
            }
        }
    )

    assert (
        'Worker Duration Seconds Total</span><strong class="mono">2.0s</strong>'
        in html
    )
    assert (
        'Worker Duration Sessions Observed</span><strong class="mono">2</strong>'
        in html
    )


def test_long_dense_timeline_renders_horizontal_and_vertical_scroll_surfaces() -> None:
    events = [
        {
            "lane": "main",
            "kind": "main_span",
            "label": "Search orchestration",
            "start_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-01-01T02:00:00Z",
        }
    ]
    events.extend(
        {
            "lane": "worker",
            "kind": "worker_session",
            "label": f"agent_{index:04d} / completed",
            "session_id": f"agent_{index:04d}",
            "start_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-01-01T00:01:00Z",
            "terminal_state": "completed",
        }
        for index in range(20)
    )

    html = _render_timeline(
        {
            "start_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-01-01T02:00:00Z",
            "duration_seconds": 7200.0,
            "events": events,
        },
        title="Dense Search Timeline",
    )

    assert 'class="timeline-scroll" tabindex="0"' in html
    assert 'style="--timeline-width:3550px"' in html
    assert 'class="timeline-rows" data-track-count="21"' in html


def test_timeline_formats_rounded_integer_metrics_without_dropping_zeroes() -> None:
    assert _metric_readout("tokens_per_minute", 167489.741612991) == "167,490/min"


def test_timeline_stacks_nearby_point_events_instead_of_overlapping() -> None:
    html = _render_timeline(
        {
            "start_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-01-01T01:00:00Z",
            "duration_seconds": 3600.0,
            "events": [
                {
                    "lane": "verifier",
                    "kind": "parent_verifier",
                    "label": f"Parent verifier #{index}",
                    "start_at": f"2026-01-01T00:59:{50 + index:02d}Z",
                    "end_at": None,
                }
                for index in range(4)
            ],
        },
        title="Point Collision Timeline",
    )

    assert "left:99.722%;width:0.800%;top:4px" in html
    assert "left:99.750%;width:0.800%;top:16px" in html
    assert "left:99.778%;width:0.800%;top:28px" in html
    assert "left:99.806%;width:0.800%;top:40px" in html
    assert 'style="min-height:54px"' in html
