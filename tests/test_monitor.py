from __future__ import annotations

import asyncio
from pathlib import Path

from agentic_any_search_mcp.goal_plus import FileGoalPlusRuntime
from agentic_any_search_mcp.monitor import goal_plus_monitor_snapshot
from agentic_any_search_mcp.pi_tool import call_pi_tool
from agentic_any_search_mcp.runtime import FileSearchRuntime
from agentic_any_search_mcp.server import create_mcp

from tests.test_runtime_unit import make_project, spec_with_strategy


def _pi_rpc_spec(project: Path):
    return spec_with_strategy(
        project,
        {
            "name": "random",
            "worker_mode": "agent-session-pool",
            "worker_host": "pi-rpc",
            "worker_budget": {
                "max_runtime_seconds": 600,
                "max_turns": 8,
                "on_exceed": "interrupt",
            },
        },
        max_candidates=2,
    )


def test_goal_plus_monitor_snapshot_summarizes_run_subagents_and_pi_metrics(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime_root = tmp_path / ".search"
    runtime = FileSearchRuntime(runtime_root)
    frozen = runtime.freeze_spec(_pi_rpc_spec(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    first, second = runtime.start_batch(run_id, plan.plan_id)
    session = runtime.start_agent_session(run_id, first.candidate_id)
    event_log = runtime_root / "host-logs" / "pi-rpc-agent.jsonl"
    session_file = runtime_root / "host-logs" / "pi-rpc-session.jsonl"
    event_log.parent.mkdir(parents=True, exist_ok=True)
    event_log.write_text('{"type":"tool_call","name":"search_run_verifier"}\n', encoding="utf-8")
    session_file.write_text('{"type":"message","role":"assistant"}\n', encoding="utf-8")

    runtime.bind_agent_handle(
        session.agent_session_id,
        {
            "host": "pi-rpc",
            "external_id": session.agent_session_id,
            "metadata": {
                "event_log": str(event_log),
                "session_file": str(session_file),
                "timed_out": False,
                "runner_failed": False,
                "soft_closeout_seconds": 45,
                "soft_closeout_sent": True,
                "raw_logging": False,
                "pi_metrics": {
                    "duration_seconds": 12.5,
                    "usage_delta": {
                        "assistantMessages": 2,
                        "input": 100,
                        "output": 25,
                        "cacheRead": 50,
                        "cacheWrite": 0,
                        "costTotal": 0.12,
                    },
                    "usage_total": {
                        "assistantMessages": 3,
                        "input": 150,
                        "output": 35,
                        "cacheRead": 75,
                        "cacheWrite": 0,
                        "costTotal": 0.18,
                    },
                    "session_stats": {
                        "contextUsage": {
                            "tokens": 12345,
                            "contextWindow": 272000,
                            "percent": 4.5386,
                        },
                        "tokens": {"total": 260},
                        "cost": 0.18,
                    },
                },
            },
        },
    )
    runtime.run_verifier(run_id, first.candidate_id, agent_session_id=session.agent_session_id)

    snapshot = goal_plus_monitor_snapshot(
        root_dir=runtime_root,
        run_id=run_id,
        stale_after_seconds=600,
    )

    assert snapshot["run"]["run_id"] == run_id
    assert snapshot["run"]["state"] == "waiting_for_workers"
    assert snapshot["run"]["candidates_total"] == 2
    assert snapshot["run"]["candidates_evaluated"] == 1
    assert snapshot["strategy"] == {
        "name": "random",
        "driver": "builtin",
        "ref": None,
        "worker_mode": "agent-session-pool",
        "worker_host": "pi-rpc",
        "worker_agent_type": None,
        "history_policy": {
            "scope": "top_n",
            "top_n": 5,
            "include": ["summary", "score", "key_metrics", "parent_id", "changed_files"],
        },
        "latest_plan": {
            "plan_id": plan.plan_id,
            "status": "started",
            "requested_k": 2,
            "planned_k": 2,
            "started_candidate_ids": [first.candidate_id, second.candidate_id],
            "selection_rule": "random bootstrap",
            "state": {},
        },
    }
    assert snapshot["main_agent"]["subagent_count"] == 1
    assert snapshot["main_agent"]["verifier_count"] == 1
    assert snapshot["main_agent"]["estimated_cost_total"] == 0.18

    [subagent] = snapshot["subagents"]
    assert subagent["agent_session_id"] == session.agent_session_id
    assert subagent["candidate_id"] == first.candidate_id
    assert subagent["attempt_count"] == 1
    assert subagent["verifier_count"] == 1
    assert subagent["context"]["tokens"] == 12345
    assert subagent["usage_total"]["costTotal"] == 0.18
    assert subagent["duration_seconds"] == 12.5
    assert subagent["soft_closeout_seconds"] == 45
    assert subagent["soft_closeout_sent"] is True
    assert subagent["raw_logging"] is False
    assert subagent["runner_failed"] is False
    assert subagent["progress_handoff"] is None
    assert subagent["liveness"] == "evaluated"

    assert snapshot["candidates"][second.candidate_id]["status"] == "created"
    assert snapshot["candidates"][second.candidate_id]["agent_session_count"] == 0
    assert any(warning["kind"] == "candidate_without_agent_session" for warning in snapshot["warnings"])


def test_goal_plus_monitor_snapshot_does_not_attach_unlinked_latest_run(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime_root = tmp_path / ".search"
    goal_runtime = FileGoalPlusRuntime(runtime_root)
    goal = goal_runtime.create_goal("Analyze a model optimization target")

    search_runtime = FileSearchRuntime(runtime_root)
    frozen = search_runtime.freeze_spec(_pi_rpc_spec(project), [project / "evaluator.py"])
    unrelated_run_id = search_runtime.create_run(frozen.frozen_spec_id)

    snapshot = goal_plus_monitor_snapshot(
        root_dir=runtime_root,
        goal_plus_id=goal.goal_plus_id,
    )

    assert snapshot["goal_plus"]["goal_plus_id"] == goal.goal_plus_id
    assert snapshot["goal_plus"]["linked_search"] is None
    assert snapshot["run"] is None
    assert snapshot["subagents"] == []
    assert snapshot["candidates"] == {}
    assert not any(warning["kind"] == "inferred_latest_run" for warning in snapshot["warnings"])
    assert unrelated_run_id is not None


def test_goal_plus_monitor_snapshot_is_exposed_to_mcp_and_pi_facade(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime_root = tmp_path / ".search"
    runtime = FileSearchRuntime(runtime_root)
    frozen = runtime.freeze_spec(_pi_rpc_spec(project), [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    mcp = create_mcp(tmp_path / ".search")
    tools = asyncio.run(mcp.get_tools())

    assert "goal_plus_monitor_snapshot" in tools
    assert "run_id" in tools["goal_plus_monitor_snapshot"].parameters["properties"]
    assert "goal_plus_id" in tools["goal_plus_monitor_snapshot"].parameters["properties"]

    result = call_pi_tool(
        runtime_root,
        "goal_plus_monitor_snapshot",
        {"run_id": run_id},
    )

    assert result["run"]["run_id"] == run_id
    assert result["strategy"]["name"] == "random"
    assert result["strategy"]["latest_plan"] is None


def test_goal_plus_monitor_snapshot_summarizes_strategy_specific_plan_state(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    runtime_root = tmp_path / ".search"
    runtime = FileSearchRuntime(runtime_root)
    spec = spec_with_strategy(
        project,
        {
            "name": "openevolve",
            "worker_mode": "agent-session-pool",
            "worker_host": "opencode",
            "history_policy": {"scope": "top_n", "top_n": 3},
            "config": {"archive_size": 10, "seed": 7},
        },
        max_candidates=2,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)

    snapshot = goal_plus_monitor_snapshot(root_dir=runtime_root, run_id=run_id)

    assert snapshot["strategy"]["name"] == "openevolve"
    assert snapshot["strategy"]["driver"] == "builtin"
    assert snapshot["strategy"]["history_policy"]["top_n"] == 3
    assert snapshot["strategy"]["latest_plan"] == {
        "plan_id": plan.plan_id,
        "status": "planned",
        "requested_k": 1,
        "planned_k": 1,
        "started_candidate_ids": [],
        "selection_rule": "openevolve bootstrap",
        "state": {"sampling_mode": "bootstrap"},
    }
