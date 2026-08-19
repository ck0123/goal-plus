from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from goal_plus.feature_plugins import orchestration_metadata
from goal_plus.goal_plus import FileGoalPlusRuntime
from goal_plus.monitor import goal_plus_monitor_snapshot


HOSTS = {
    "codex": {
        "native_reasoning_effort": "max",
        "dispatch": "spawn_agent",
        "result": "wait_agent",
    },
    "pi": {
        "native_reasoning_effort": "xhigh",
        "dispatch": "pi_goal_plus_run_work_item",
        "result": "goal-plus-pi-worker",
    },
}


def run_host(root: Path, host_id: str) -> dict[str, Any]:
    profile = HOSTS[host_id]
    runtime = FileGoalPlusRuntime(root / host_id / ".gp")
    goal = runtime.create_goal(
        "Add focused duration parser edge-case tests",
        source_path=str(root / host_id),
        policy={
            "execution": {
                "host": {
                    "host_id": host_id,
                    "native_reasoning_effort": profile["native_reasoning_effort"],
                    "enforcement": "host",
                    "operations": ["spawn", "send", "wait", "interrupt", "observe"],
                }
            }
        },
    )
    runtime.record_triage(
        goal.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
        },
    )
    runtime.upsert_work_items(
        goal.goal_plus_id,
        [
            {
                "work_item_id": "edge_tests",
                "title": "Cover parser boundaries",
                "objective": "Add tests for signed and fractional durations.",
                "route": "subagent",
                "scope": ["tests/test_duration.py"],
                "acceptance": ["Focused tests pass", "Production code stays unchanged"],
            }
        ],
    )
    agent_id = f"{host_id}-worker-1"
    dispatched = runtime.record_work_event(
        goal.goal_plus_id,
        "edge_tests",
        "dispatch",
        f"{host_id} dispatched the test task.",
        host=host_id,
        task_name="edge_test_worker",
        metadata=orchestration_metadata(
            str(profile["dispatch"]),
            direction="main_to_subagent",
            semantic_event="assignment",
        ),
    )
    item = dispatched.work_items[0]
    runtime.record_work_event(
        goal.goal_plus_id,
        "edge_tests",
        "bind",
        f"{host_id} bound the launched worker.",
        host=host_id,
        task_name="edge_test_worker",
        agent_id=agent_id,
        attempt_id=item.attempt_id,
        generation=item.generation,
        metadata=orchestration_metadata(str(profile["dispatch"])),
    )
    runtime.record_work_event(
        goal.goal_plus_id,
        "edge_tests",
        "result",
        "Worker added three focused tests; all three passed.",
        host=host_id,
        task_name="edge_test_worker",
        agent_id=agent_id,
        attempt_id=item.attempt_id,
        generation=item.generation,
        metadata=orchestration_metadata(
            str(profile["result"]),
            direction="subagent_to_main",
            semantic_event="result",
        ),
        evidence=[{"kind": "pytest", "outcome": "3 passed"}],
    )
    runtime.record_work_event(
        goal.goal_plus_id,
        "edge_tests",
        "accepted",
        "Main reproduced the focused tests and accepted the result.",
        evidence=[{"kind": "main_verification", "outcome": "3 passed"}],
    )
    snapshot = goal_plus_monitor_snapshot(
        runtime.root_dir,
        goal_plus_id=goal.goal_plus_id,
        feature_plugins=["orchestration"],
    )
    return snapshot["feature_plugins"]["orchestration"]


def semantic_flow(payload: dict[str, Any]) -> list[list[str]]:
    return [
        [event["semantic_event"], event["direction"], event["work_event"]]
        for event in payload["interactions"]
    ]


def compare(output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output_dir}")
    output_dir.mkdir(parents=True)
    codex = run_host(output_dir, "codex")
    pi = run_host(output_dir, "pi")
    [codex_item] = codex["work_items"]
    [pi_item] = pi["work_items"]
    result = {
        "example_kind": "deterministic_protocol_comparison",
        "live_model_execution": False,
        "task_packet_equal": codex_item["task_packet"] == pi_item["task_packet"],
        "semantic_flow_equal": semantic_flow(codex) == semantic_flow(pi),
        "semantic_flow": semantic_flow(codex),
        "codex": {
            "host": codex["host"],
            "native_operations": codex["summary"]["native_operations"],
        },
        "pi": {
            "host": pi["host"],
            "native_operations": pi["summary"]["native_operations"],
        },
    }
    (output_dir / "comparison.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Codex and Pi Goal Plus orchestration semantics."
    )
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.output_dir.resolve()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
