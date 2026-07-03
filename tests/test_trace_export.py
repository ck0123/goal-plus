from __future__ import annotations

import json
import tomllib
from pathlib import Path

from agentic_any_search_mcp.models import SearchSpec
from agentic_any_search_mcp.runtime import FileSearchRuntime
from agentic_any_search_mcp.trace_export import export_chrome_trace


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "initial_program.py").write_text("VALUE = 0\n", encoding="utf-8")
    (project / "evaluator.py").write_text(
        "import importlib.util, json\n"
        "def evaluate(path):\n"
        "    spec = importlib.util.spec_from_file_location('initial_program', path)\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(module)\n"
        "    return {'combined_score': float(module.VALUE), 'valid': True}\n"
        "print(json.dumps(evaluate('initial_program.py'), sort_keys=True))\n",
        encoding="utf-8",
    )
    (project / "config.yaml").write_text("name: trace\n", encoding="utf-8")
    return project


def spec_for(project: Path) -> SearchSpec:
    return SearchSpec.model_validate(
        {
            "objective": "export a chrome trace",
            "metric_name": "combined_score",
            "metric_direction": "maximize",
            "source_path": str(project),
            "edit_surface": {
                "allow": ["initial_program.py"],
                "deny": ["evaluator.py", "config.yaml"],
            },
            "budget": {
                "max_candidates": 1,
                "max_parallel": 1,
            },
            "process_verifiers": [
                {
                    "name": "score",
                    "role": "ranking_signal",
                    "command": ["python", "evaluator.py"],
                    "timeout_seconds": 30,
                }
            ],
            "promotion_verifiers": [
                {
                    "name": "hash_check",
                    "role": "anti_cheat_gate",
                    "command": ["search-runtime-internal", "check-frozen-hashes"],
                }
            ],
            "strategy": {
                "name": "independent_branches",
                "worker_mode": "agent-session-pool",
                "worker_agent_type": "AnySearchAgentFlash",
            },
        }
    )


def event_named(trace: dict, name: str) -> dict:
    return next(event for event in trace["traceEvents"] if event.get("name") == name)


def test_export_chrome_trace_includes_run_agent_verifier_and_report_events(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    root = tmp_path / ".search"
    runtime = FileSearchRuntime(root)
    frozen = runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(
        run_id,
        task.candidate_id,
        {"intent": "set VALUE to 1"},
    )
    runtime.bind_opencode_session(session.agent_session_id, "ses_trace_001")
    (task.workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        task.candidate_id,
        agent_session_id=session.agent_session_id,
    )
    runtime.select(run_id)
    runtime.report(run_id)

    trace_path = export_chrome_trace(root, run_id)
    trace = json.loads(trace_path.read_text(encoding="utf-8"))

    assert trace_path == root / "runs" / run_id / "trace.json"
    assert isinstance(trace["traceEvents"], list)
    assert trace["displayTimeUnit"] == "ms"

    run_event = event_named(trace, f"run {run_id}")
    assert run_event["ph"] == "X"
    assert run_event["cat"] == "run"
    assert run_event["args"]["best_score"] == 1.0
    assert run_event["args"]["selected_candidate_id"] == "c001"

    plan_event = event_named(trace, "plan_001")
    assert plan_event["cat"] == "plan"
    assert plan_event["args"]["planned_k"] == 1
    assert plan_event["args"]["started_candidate_ids"] == ["c001"]

    agent_event = event_named(trace, "c001 AnySearchAgentFlash")
    assert agent_event["cat"] == "subagent"
    assert agent_event["args"]["candidate_id"] == "c001"
    assert agent_event["args"]["opencode_session_id"] == "ses_trace_001"

    verifier_event = event_named(trace, "score c001#1")
    assert verifier_event["cat"] == "verifier"
    assert verifier_event["args"]["score"] == 1.0
    assert verifier_event["dur"] > 0

    report_event = event_named(trace, "report.md")
    assert report_event["ph"] == "i"
    assert report_event["cat"] == "artifact"
    assert report_event["args"]["path"].endswith("/report.md")

    thread_names = [
        event["args"]["name"]
        for event in trace["traceEvents"]
        if event.get("ph") == "M" and event.get("name") == "thread_name"
    ]
    assert "MCP runtime" in thread_names
    assert "c001 subagent" in thread_names


def test_export_chrome_trace_uses_opencode_log_for_subagent_timing(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    root = tmp_path / ".search"
    runtime = FileSearchRuntime(root)
    frozen = runtime.freeze_spec(spec_for(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(
        run_id,
        task.candidate_id,
        {"intent": "set VALUE to 1"},
    )
    runtime.bind_opencode_session(session.agent_session_id, "ses_trace_001")
    (task.workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        task.candidate_id,
        agent_session_id=session.agent_session_id,
    )
    runtime.select(run_id)
    runtime.report(run_id)

    opencode_log = tmp_path / "opencode.log"
    opencode_log.write_text(
        "\n".join(
            [
                (
                    "timestamp=2026-07-03T09:23:22.665Z level=INFO run=runlog "
                    "message=created id=ses_trace_001 title=\"c001 baseline\" "
                    "agent=AnySearchAgentFlash time.created=1783070602665 "
                    "time.updated=1783070602665"
                ),
                (
                    "timestamp=2026-07-03T09:23:23.000Z level=INFO run=runlog "
                    "message=loop session.id=ses_trace_001 step=0"
                ),
                (
                    "timestamp=2026-07-03T09:23:24.000Z level=INFO run=runlog "
                    "message=process session.id=ses_trace_001 messageID=msg_001"
                ),
                (
                    "timestamp=2026-07-03T09:23:25.000Z level=INFO run=runlog "
                    "message=stream providerID=zai-coding-plan modelID=glm-5.2 "
                    "session.id=ses_trace_001 small=false agent=AnySearchAgentFlash "
                    "mode=subagent"
                ),
                (
                    "timestamp=2026-07-03T09:26:09.113Z level=INFO run=runlog "
                    "message=\"exiting loop\" session.id=ses_trace_001"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    trace_path = export_chrome_trace(root, run_id, opencode_log_path=opencode_log)
    trace = json.loads(trace_path.read_text(encoding="utf-8"))

    agent_event = event_named(trace, "c001 AnySearchAgentFlash")
    assert agent_event["cat"] == "subagent"
    assert agent_event["dur"] == 166_448_000
    assert agent_event["args"]["timing_source"] == "opencode_log"
    assert agent_event["args"]["opencode_started_at"] == "2026-07-03T09:23:22.665Z"
    assert agent_event["args"]["opencode_completed_at"] == "2026-07-03T09:26:09.113Z"
    assert agent_event["args"]["opencode_steps"] == [0]
    assert agent_event["args"]["opencode_title"] == "c001 baseline"
    assert agent_event["args"]["agent_session_id"] == session.agent_session_id

    loop_event = event_named(trace, "c001 opencode loop 0")
    assert loop_event["cat"] == "opencode"
    assert loop_event["ph"] == "i"
    assert loop_event["args"]["source"] == "opencode_log"
    assert loop_event["args"]["opencode_session_id"] == "ses_trace_001"

    process_event = event_named(trace, "c001 opencode process")
    assert process_event["args"]["messageID"] == "msg_001"


def test_pyproject_exposes_trace_export_console_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert (
        pyproject["project"]["scripts"]["agentic-any-search-trace"]
        == "agentic_any_search_mcp.trace_export:main"
    )
