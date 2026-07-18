from __future__ import annotations

from pathlib import Path

import pytest

from goal_plus.goal_plus import FileGoalPlusRuntime
from goal_plus.models import GoalPlusRecord
from goal_plus.reporting import (
    _build_timeline,
    _epoch,
    _render_timeline,
    build_html_report_data,
)
from goal_plus.runtime import FileSearchRuntime

from tests._runtime_helpers import make_project, spec_for


def test_search_report_generates_self_contained_html_with_multi_search_timeline(
    tmp_path: Path,
) -> None:
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
    assert "rolling_candidates" in html
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
    assert "&lt;script&gt;alert(&#x27;unsafe&#x27;)&lt;/script&gt;" in html
    assert "<script>alert('unsafe')</script>" not in html
    assert "https://" not in html


def test_html_report_data_keeps_search_tasks_and_rounds_separate(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    root = tmp_path / ".search"
    search = FileSearchRuntime(root)
    frozen = search.freeze_spec(spec_for(project), [project / "evaluator.py"])
    runs = [search.create_run(frozen.frozen_spec_id) for _ in range(2)]
    for run_id in runs:
        search.plan_next(run_id, requested_k=1)
        search.plan_next(run_id, requested_k=1)

    goals = FileGoalPlusRuntime(root)
    goal = goals.create_goal("Compare two independent Search tasks")
    for run_id in runs:
        goals.link_search_run(goal.goal_plus_id, frozen.frozen_spec_id, run_id)

    data = build_html_report_data(root, runs[-1])

    assert data["goal_plus_id"] == goal.goal_plus_id
    assert [task["run_id"] for task in data["search_tasks"]] == runs
    assert all(
        task["strategy"]["orchestration_mode"] == "rolling_candidates"
        for task in data["search_tasks"]
    )
    assert [len(task["plans"]) for task in data["search_tasks"]] == [2, 2]
    assert all(task["timeline"]["duration_seconds"] for task in data["search_tasks"])
    assert data["snapshot"]["search_task_aggregate"]["search_tasks_total"] == 2
    assert data["snapshot"]["search_task_aggregate"]["planning_rounds_total"] == 4


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
                            "created_at": "2026-01-01T00:01:30Z",
                        },
                        {
                            "iteration": 2,
                            "agent_session_id": "agent_metric_002",
                            "score": 0.8,
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
    assert performance["idle_intervals"][0]["duration_seconds"] == 480.0

    html = _render_timeline(timeline, title="Metric Lens Timeline")

    assert "data-metric-lens" in html
    assert 'data-metric-mode="score-gain"' in html
    assert 'data-metric-score-gain="0.100000000"' in html
    assert 'data-metric-tokens-per-minute="600.000000000"' in html
    assert 'data-metric-verifier-density="2.000000000" style=' in html
    assert 'class="score-step"' in html
    assert "Baseline 0.5" in html
    assert "Selected 0.8" in html
    assert "retry 2/2" in html
    assert "session-failure" in html
    assert "Idle 8m 0s" in html


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
    assert 'style="--timeline-width:9790px"' in html
    assert 'class="timeline-rows" data-track-count="21"' in html
