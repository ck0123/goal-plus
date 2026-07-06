#!/usr/bin/env python3
"""Stop hook backstop for Goal Plus.

The hook is intentionally narrow: it only checks whether an active Goal Plus
record says the top-level agent still has a required next action. It does not
supervise workers or reimplement goal completion logic.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


GOAL_ID_RE = re.compile(r"\bgp_\d+\b")
DISABLE_VALUES = {"1", "true", "yes", "on"}


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def _add_local_src_to_path() -> None:
    src = _repo_root_from_script() / "src"
    if src.is_dir():
        sys.path.insert(0, str(src))


def _read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _hook_disabled() -> bool:
    value = os.environ.get("GOAL_PLUS_STOP_HOOK_DISABLED", "")
    return value.lower() in DISABLE_VALUES


def _find_session_root() -> Path:
    override = os.environ.get("GOAL_PLUS_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    claude_project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if claude_project_dir:
        return Path(claude_project_dir).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".search").exists() or (candidate / ".git").exists():
            return candidate
    return cwd


def _search_root(session_root: Path) -> Path:
    override = os.environ.get("GOAL_PLUS_SEARCH_ROOT")
    if override:
        root = Path(override).expanduser()
        return root.resolve() if root.is_absolute() else (session_root / root).resolve()
    return (session_root / ".search").resolve()


def _first_goal_id(value: Any) -> str | None:
    if isinstance(value, str):
        match = GOAL_ID_RE.search(value)
        return match.group(0) if match else None
    if isinstance(value, dict):
        for key in ("goal_plus_id", "goalPlusId", "goal_id", "goalId"):
            found = _first_goal_id(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _first_goal_id(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_goal_id(item)
            if found:
                return found
    return None


def _candidate_goal_ids(hook_input: dict[str, Any]) -> list[str]:
    explicit = os.environ.get("GOAL_PLUS_ID")
    from_input = _first_goal_id(hook_input)
    return [goal_id for goal_id in (explicit, from_input) if goal_id]


def _load_record(path: Path):
    from agentic_any_search_mcp.goal_plus import read_json
    from agentic_any_search_mcp.models import GoalPlusRecord

    try:
        return GoalPlusRecord.model_validate(read_json(path))
    except Exception:
        return None


def _select_goal_id(search_root: Path, hook_input: dict[str, Any]) -> str | None:
    goals_dir = search_root / "goal-plus"
    if not goals_dir.is_dir():
        return None

    for goal_id in _candidate_goal_ids(hook_input):
        if (goals_dir / goal_id / "goal.json").is_file():
            return goal_id

    active = []
    for path in goals_dir.glob("gp_*/goal.json"):
        record = _load_record(path)
        if record is not None and record.status == "active":
            active.append(record)
    if not active:
        return None

    active.sort(key=lambda record: (record.updated_at, record.goal_plus_id), reverse=True)
    return active[0].goal_plus_id


def _emit_block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def main() -> int:
    if _hook_disabled():
        return 0

    _add_local_src_to_path()
    hook_input = _read_hook_input()
    session_root = _find_session_root()
    search_root = _search_root(session_root)

    try:
        goal_id = _select_goal_id(search_root, hook_input)
        if goal_id is None:
            return 0

        from agentic_any_search_mcp.goal_plus import FileGoalPlusRuntime

        runtime = FileGoalPlusRuntime(search_root)
        gate = runtime.gate(goal_id, event="stop", context=hook_input)
        if gate.decision == "block":
            _emit_block(
                gate.continuation_prompt
                or gate.reason
                or "Goal Plus is still active; continue before stopping."
            )
        return 0
    except Exception as exc:
        print(f"[goal-plus-stop] allowing Stop because hook failed: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
