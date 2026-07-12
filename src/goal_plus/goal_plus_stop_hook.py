"""Host hook backstop for Goal Plus adapters."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from goal_plus.paths import DEFAULT_RUNTIME_ROOT, LEGACY_RUNTIME_ROOT


GOAL_ID_RE = re.compile(r"\bgp_\d+\b")
GOAL_PLUS_PROMPT_RE = re.compile(
    r"^\s*(?:/|\$)goal-plus(?:\s+(?P<goal>.*\S))?\s*$",
    re.IGNORECASE | re.DOTALL,
)
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
        if (
            (candidate / DEFAULT_RUNTIME_ROOT).exists()
            or (candidate / LEGACY_RUNTIME_ROOT).exists()
            or (candidate / ".git").exists()
        ):
            return candidate
    return cwd


def _search_root(session_root: Path) -> Path:
    override = os.environ.get("GOAL_PLUS_SEARCH_ROOT")
    if override:
        root = Path(override).expanduser()
        return root.resolve() if root.is_absolute() else (session_root / root).resolve()
    default_root = (session_root / DEFAULT_RUNTIME_ROOT).resolve()
    if default_root.exists():
        return default_root
    legacy_root = (session_root / LEGACY_RUNTIME_ROOT).resolve()
    if legacy_root.exists():
        return legacy_root
    return default_root


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


def _prompt(hook_input: dict[str, Any]) -> str:
    value = hook_input.get("prompt")
    return value if isinstance(value, str) else ""


def _goal_plus_prompt(hook_input: dict[str, Any]) -> str | None:
    match = GOAL_PLUS_PROMPT_RE.match(_prompt(hook_input))
    if match is None:
        return None
    raw_goal = (match.group("goal") or "").strip()
    return raw_goal or None


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
    from goal_plus.goal_plus import read_json
    from goal_plus.models import GoalPlusRecord

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


def _select_hook_goal_id(search_root: Path, hook_input: dict[str, Any]) -> str | None:
    return _select_explicit_goal_id(search_root, hook_input) or _select_session_goal_id(
        search_root,
        _session_id(hook_input),
    )


def _goal_context(record: Any) -> str:
    next_action = record.next_action
    next_action_text = (
        f"{next_action.kind}: {next_action.description}"
        if next_action is not None
        else "none"
    )
    return (
        f"Goal Plus is active for this Codex session: goal_plus_id={record.goal_plus_id}.\n"
        "The runtime record already exists; do not call goal_plus_create again.\n"
        f"Current phase: {record.phase}; next action: {next_action_text}\n"
        "Use the goal_plus_* tools and the linked Search runtime as authoritative."
    )


def _emit_additional_context(event_name: str, context: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": context,
                }
            },
            ensure_ascii=False,
        )
    )


def _emit_pre_tool_block(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )


def _should_gate_tool(tool_name: str) -> bool:
    normalized = tool_name.strip().lower().replace("-", "_")
    logical_name = normalized.rsplit("__", 1)[-1]
    return (
        logical_name.startswith("search_")
        or logical_name in {
            "pi_rpc_run_worker",
            "pi_search_run_candidate",
            "bash",
            "edit",
            "write",
            "exec_command",
            "apply_patch",
        }
    )


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


def _emit_terminal_stats(record: Any) -> None:
    counters = ",".join(
        f"{name}={count}" for name, count in sorted(record.hook_counters.items())
    ) or "none"
    linked_run = (
        record.linked_search.run_id
        if record.linked_search is not None and record.linked_search.run_id
        else "none"
    )
    message = (
        "Goal Plus stats: "
        f"goal_plus_id={record.goal_plus_id}; "
        f"status={record.status}; "
        f"phase={record.phase}; "
        f"search_tasks={len(record.search_tasks)}; "
        f"linked_run={linked_run}; "
        f"gates={counters}"
    )
    print(json.dumps({"systemMessage": message}, ensure_ascii=False))


def _handle_user_prompt_submit(
    runtime: Any,
    search_root: Path,
    hook_input: dict[str, Any],
) -> None:
    session_id = _session_id(hook_input)
    goal_id = _select_session_goal_id(search_root, session_id)
    raw_goal = _goal_plus_prompt(hook_input)
    if goal_id is None and raw_goal is not None and session_id is not None:
        record = runtime.create_goal(raw_goal)
        goal_id = record.goal_plus_id
    if goal_id is None or session_id is None:
        return
    record = runtime.activate_session(
        goal_id,
        {
            "host": _host_kind(hook_input),
            "session_id": session_id,
            "transcript_path": _transcript_path(hook_input),
        },
    )
    runtime.gate(goal_id, event="user_prompt_submit", context=hook_input)
    _emit_additional_context("UserPromptSubmit", _goal_context(record))


def _handle_session_start(
    runtime: Any,
    search_root: Path,
    hook_input: dict[str, Any],
) -> None:
    goal_id = _select_hook_goal_id(search_root, hook_input)
    if goal_id is None:
        return
    _emit_additional_context("SessionStart", _goal_context(runtime.status(goal_id)))


def _handle_pre_tool_use(
    runtime: Any,
    search_root: Path,
    hook_input: dict[str, Any],
) -> None:
    if _is_subagent_context(hook_input) or not _should_gate_tool(_tool_name(hook_input)):
        return
    goal_id = _select_hook_goal_id(search_root, hook_input)
    if goal_id is None:
        return
    gate = runtime.gate(goal_id, event="pre_tool_use", context=hook_input)
    if gate.decision == "block":
        _emit_pre_tool_block(gate.reason or "Goal Plus blocked this tool call.")


def _handle_stop_event(
    runtime: Any,
    search_root: Path,
    hook_input: dict[str, Any],
    *,
    event: str,
) -> None:
    goal_id = _select_hook_goal_id(search_root, hook_input)
    if goal_id is None:
        current_session_id = _session_id(hook_input)
        active = _active_records(search_root)
        if event == "stop" and active:
            _record_session_gate_skipped(
                runtime,
                search_root,
                hook_input,
                current_session_id,
            )
        return
    gate = runtime.gate(goal_id, event=event, context=hook_input)
    if gate.decision == "block":
        _emit_block(
            gate.continuation_prompt
            or gate.reason
            or "Goal Plus is still active; continue before stopping."
        )
    elif event == "stop" and gate.status != "active":
        _emit_terminal_stats(runtime.status(goal_id))


def main() -> int:
    if _hook_disabled():
        return 0

    hook_input = _read_hook_input()
    session_root = _find_session_root()
    search_root = _search_root(session_root)

    try:
        event_name = _hook_event_name(hook_input)
        if event_name in {"UserPromptSubmit", "SessionStart", "PreToolUse"}:
            from goal_plus.goal_plus import FileGoalPlusRuntime

            if event_name != "UserPromptSubmit" and not search_root.exists():
                return 0
            if event_name == "UserPromptSubmit":
                if not search_root.exists() and _goal_plus_prompt(hook_input) is None:
                    return 0
                runtime = FileGoalPlusRuntime(search_root)
                _handle_user_prompt_submit(runtime, search_root, hook_input)
            elif event_name == "SessionStart":
                runtime = FileGoalPlusRuntime(search_root)
                _handle_session_start(runtime, search_root, hook_input)
            else:
                runtime = FileGoalPlusRuntime(search_root)
                _handle_pre_tool_use(runtime, search_root, hook_input)
            return 0

        if event_name == "PostToolUse":
            target = _post_tool_use_bind_target(hook_input)
            if target is None:
                return 0
            goal_id, session_id = target
            goal_path = search_root / "goal-plus" / goal_id / "goal.json"
            if not goal_path.is_file():
                return 0
            from goal_plus.goal_plus import FileGoalPlusRuntime

            runtime = FileGoalPlusRuntime(search_root)
            _handle_post_tool_use(runtime, hook_input, goal_id, session_id)
            return 0

        if event_name not in {"Stop", "SubagentStop"}:
            return 0
        if not search_root.exists():
            return 0

        from goal_plus.goal_plus import FileGoalPlusRuntime

        runtime = FileGoalPlusRuntime(search_root)
        _handle_stop_event(
            runtime,
            search_root,
            hook_input,
            event="subagent_stop" if event_name == "SubagentStop" else "stop",
        )
        return 0
    except Exception as exc:
        print(f"[goal-plus-hook] allowing host action because hook failed: {exc}", file=sys.stderr)
        return 0
