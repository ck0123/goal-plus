"""Host hook backstop for Goal Plus adapters."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


GOAL_ID_RE = re.compile(r"\bgp_\d+\b")
DISABLE_VALUES = {"1", "true", "yes", "on"}


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
    return any(
        os.environ.get(name, "").lower() in DISABLE_VALUES
        for name in ("GOAL_PLUS_STOP_HOOK_DISABLED", "GOAL_PLUS_HOST_HOOK_DISABLED")
    )


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


def _explicit_goal_ids(hook_input: dict[str, Any]) -> list[str]:
    explicit = os.environ.get("GOAL_PLUS_ID")
    return [goal_id for goal_id in (explicit, _first_goal_id(hook_input)) if goal_id]


def _hook_event_name(hook_input: dict[str, Any]) -> str:
    for key in ("hook_event_name", "hookEventName", "event_name", "eventName", "event"):
        value = hook_input.get(key)
        if isinstance(value, str) and value:
            return value
    return "Stop"


def _tool_name(hook_input: dict[str, Any]) -> str:
    for key in ("tool_name", "toolName", "name"):
        value = hook_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def _is_goal_plus_create_tool(tool_name: str) -> bool:
    normalized = tool_name.replace("-", "_")
    return normalized.endswith("goal_plus_create")


def _session_id(hook_input: dict[str, Any]) -> str | None:
    for key in ("session_id", "sessionId"):
        value = hook_input.get(key)
        if isinstance(value, str) and value:
            return value
    session = hook_input.get("session")
    if isinstance(session, dict):
        for key in ("id", "session_id", "sessionId"):
            value = session.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _transcript_path(hook_input: dict[str, Any]) -> str | None:
    for key in ("transcript_path", "transcriptPath"):
        value = hook_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _tool_use_id(hook_input: dict[str, Any]) -> str | None:
    for key in ("tool_use_id", "toolUseId"):
        value = hook_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _is_subagent_context(hook_input: dict[str, Any]) -> bool:
    if _hook_event_name(hook_input) == "SubagentStop":
        return True
    for key in (
        "agent_id",
        "agentId",
        "agent_type",
        "agentType",
        "agent_transcript_path",
        "agentTranscriptPath",
    ):
        if hook_input.get(key):
            return True
    target = hook_input.get("target")
    if isinstance(target, dict):
        target_type = target.get("type") or target.get("kind")
        if target_type in {"agent", "subagent"}:
            return True
        if target.get("agent") or target.get("agent_id") or target.get("agentId"):
            return True
    return False


def _host_kind(hook_input: dict[str, Any]) -> str:
    value = hook_input.get("host")
    if value in {"opencode", "codex", "claude-code"}:
        return value
    transcript = _transcript_path(hook_input) or ""
    if os.environ.get("CLAUDE_PROJECT_DIR") or ".claude" in transcript:
        return "claude-code"
    return "codex"


def _load_record(path: Path):
    from agentic_any_search_mcp.goal_plus import read_json
    from agentic_any_search_mcp.models import GoalPlusRecord

    try:
        return GoalPlusRecord.model_validate(read_json(path))
    except Exception:
        return None


def _active_records(search_root: Path) -> list[Any]:
    goals_dir = search_root / "goal-plus"
    if not goals_dir.is_dir():
        return []
    active = []
    for path in goals_dir.glob("gp_*/goal.json"):
        record = _load_record(path)
        if record is not None and record.status == "active":
            active.append(record)

    active.sort(key=lambda record: (record.updated_at, record.goal_plus_id), reverse=True)
    return active


def _select_explicit_goal_id(search_root: Path, hook_input: dict[str, Any]) -> str | None:
    goals_dir = search_root / "goal-plus"
    if not goals_dir.is_dir():
        return None
    for goal_id in _explicit_goal_ids(hook_input):
        if (goals_dir / goal_id / "goal.json").is_file():
            return goal_id
    return None


def _select_session_goal_id(search_root: Path, session_id: str | None) -> str | None:
    if not session_id:
        return None
    for record in _active_records(search_root):
        if (
            record.active_session is not None
            and record.active_session.session_id == session_id
        ):
            return record.goal_plus_id
    return None


def _handle_post_tool_use(
    runtime: Any,
    hook_input: dict[str, Any],
    goal_id: str,
    session_id: str,
) -> None:
    runtime.activate_session(
        goal_id,
        {
            "host": _host_kind(hook_input),
            "session_id": session_id,
            "transcript_path": _transcript_path(hook_input),
            "tool_use_id": _tool_use_id(hook_input),
        },
    )


def _post_tool_use_bind_target(hook_input: dict[str, Any]) -> tuple[str, str] | None:
    if not _is_goal_plus_create_tool(_tool_name(hook_input)):
        return None
    if _is_subagent_context(hook_input):
        return None
    goal_id = _first_goal_id(hook_input.get("tool_response"))
    if goal_id is None:
        goal_id = _first_goal_id(hook_input.get("toolResponse"))
    session_id = _session_id(hook_input)
    if not goal_id or not session_id:
        return None
    return goal_id, session_id


def _record_session_gate_skipped(
    runtime: Any,
    search_root: Path,
    hook_input: dict[str, Any],
    current_session_id: str | None,
) -> None:
    active = _active_records(search_root)
    if not active:
        return
    runtime.record_session_gate_skipped(
        active[0].goal_plus_id,
        "no_matching_session",
        current_session_id=current_session_id,
        context={"hook_event_name": _hook_event_name(hook_input)},
    )


def _emit_block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def main() -> int:
    if _hook_disabled():
        return 0

    hook_input = _read_hook_input()
    session_root = _find_session_root()
    search_root = _search_root(session_root)

    try:
        event_name = _hook_event_name(hook_input)
        if event_name == "PostToolUse":
            target = _post_tool_use_bind_target(hook_input)
            if target is None:
                return 0
            goal_id, session_id = target
            goal_path = search_root / "goal-plus" / goal_id / "goal.json"
            if not goal_path.is_file():
                return 0
            from agentic_any_search_mcp.goal_plus import FileGoalPlusRuntime

            runtime = FileGoalPlusRuntime(search_root)
            _handle_post_tool_use(runtime, hook_input, goal_id, session_id)
            return 0

        if event_name != "Stop":
            return 0

        goal_id = _select_explicit_goal_id(search_root, hook_input)
        if goal_id is None:
            current_session_id = _session_id(hook_input)
            goal_id = _select_session_goal_id(search_root, current_session_id)
            if goal_id is None:
                active = _active_records(search_root)
                if not active:
                    return 0
                from agentic_any_search_mcp.goal_plus import FileGoalPlusRuntime

                runtime = FileGoalPlusRuntime(search_root)
                _record_session_gate_skipped(
                    runtime,
                    search_root,
                    hook_input,
                    current_session_id,
                )
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
        print(f"[goal-plus-hook] allowing host action because hook failed: {exc}", file=sys.stderr)
        return 0
