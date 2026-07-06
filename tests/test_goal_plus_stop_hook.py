from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agentic_any_search_mcp.goal_plus import FileGoalPlusRuntime


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "hooks" / "goal_plus_stop.py"


def _run_hook(tmp_path: Path, search_root: Path, hook_input: dict | None = None, **env):
    run_env = {
        **os.environ,
        "GOAL_PLUS_SEARCH_ROOT": str(search_root),
        "GOAL_PLUS_PROJECT_ROOT": str(tmp_path),
        **{key: str(value) for key, value in env.items()},
    }
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=tmp_path,
        input=json.dumps(hook_input or {}),
        text=True,
        capture_output=True,
        check=False,
        env=run_env,
    )


def test_stop_hook_allows_when_no_goal_state_and_does_not_create_state(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"

    result = _run_hook(tmp_path, search_root)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert not search_root.exists()


def test_stop_hook_blocks_latest_active_goal_with_required_next_action(
    tmp_path: Path,
) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")

    result = _run_hook(tmp_path, search_root, {"stop_reason": "done"})

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "Goal Plus is still active" in payload["reason"]
    assert "Classify whether the raw goal" in payload["reason"]

    events = runtime.list_events(record.goal_plus_id)
    assert events[-1]["event_type"] == "gate_blocked"


def test_stop_hook_allows_goal_mode_without_required_next_action(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Tidy docs wording")
    runtime.record_triage(
        record.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
            "reasons": ["qualitative docs task"],
        },
    )

    result = _run_hook(tmp_path, search_root)

    assert result.returncode == 0
    assert result.stdout == ""
    assert runtime.list_events(record.goal_plus_id)[-1]["event_type"] == "gate_allowed"


def test_stop_hook_can_target_explicit_goal_id(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    first = runtime.create_goal("Optimize kernel")
    second = runtime.create_goal("Tidy docs")
    runtime.record_triage(
        second.goal_plus_id,
        {
            "is_optimization": False,
            "confidence": "high",
            "recommended_phase": "goal",
            "reasons": ["qualitative docs task"],
        },
    )

    result = _run_hook(tmp_path, search_root, GOAL_PLUS_ID=first.goal_plus_id)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert runtime.list_events(first.goal_plus_id)[-1]["event_type"] == "gate_blocked"


def test_stop_hook_disable_env_allows_without_gate_event(tmp_path: Path) -> None:
    search_root = tmp_path / ".search"
    runtime = FileGoalPlusRuntime(search_root)
    record = runtime.create_goal("Optimize model throughput")
    before = runtime.list_events(record.goal_plus_id)

    result = _run_hook(
        tmp_path,
        search_root,
        GOAL_PLUS_STOP_HOOK_DISABLED="1",
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert runtime.list_events(record.goal_plus_id) == before
