from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
INSTALL_HINT = (
    'Install this project into the Python environment that launches Pi: '
    'python -m pip install -e ".[dev]".'
)
AUDIT_PROMPT = (
    "请只读审计当前分支的 Pi native Goal Plus 改造是否完整。范围限定为 "
    ".pi/extensions/search-runtime.ts、.pi/skills/goal-plus/SKILL.md、docs/pi.md、"
    "tests/test_pi_assets.py。重点检查：/goal-plus 命令、session_start 状态恢复、"
    "before_agent_start 上下文注入、tool_call pre-tool gate、agent_end stop gate、"
    "terminal stats 输出。不要修改文件。最后给出结论，并把 Goal Plus 状态设为 complete。"
)


def _pi_base_command(session_dir: Path, session_id: str) -> list[str]:
    command = [
        os.environ.get("ST_PI_BINARY", "pi"),
        "--approve",
        "--session-dir",
        str(session_dir),
        "--session-id",
        session_id,
    ]
    model = os.environ.get("ST_PI_MODEL")
    if model:
        command.extend(["--model", model])
    thinking = os.environ.get("ST_PI_THINKING")
    if thinking:
        command.extend(["--thinking", thinking])
    return command


def _run_env(search_root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "AGENTIC_ANY_SEARCH_ROOT": str(search_root),
        "AGENTIC_ANY_SEARCH_SOURCE_PATH": str(ROOT),
    }


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _session_file(session_dir: Path) -> Path:
    files = sorted(session_dir.glob("*.jsonl"), key=lambda path: path.stat().st_mtime)
    assert files, f"no Pi session JSONL found in {session_dir}"
    return files[-1]


def _goal_record(search_root: Path) -> dict:
    goal_files = sorted((search_root / "goal-plus").glob("gp_*/goal.json"))
    assert len(goal_files) == 1
    return json.loads(goal_files[0].read_text(encoding="utf-8"))


def _goal_events(search_root: Path, goal_plus_id: str) -> list[dict]:
    return _read_jsonl(search_root / "goal-plus" / goal_plus_id / "events.jsonl")


def _tool_calls(entries: list[dict], name: str) -> list[dict]:
    calls = []
    for entry in entries:
        message = entry.get("message") or {}
        if message.get("role") != "assistant":
            continue
        for item in message.get("content") or []:
            if item.get("type") == "toolCall" and item.get("name") == name:
                calls.append(item)
    return calls


def _assert_goal_plus_jsonl_is_clean(session_path: Path) -> None:
    session_text = session_path.read_text(encoding="utf-8")
    assert "unexpected keyword argument" not in session_text
    assert INSTALL_HINT not in session_text

    entries = _read_jsonl(session_path)
    triage_calls = _tool_calls(entries, "goal_plus_record_triage")
    assert triage_calls
    for call in triage_calls:
        arguments = call.get("arguments") or {}
        assert "triage" in arguments
        assert "classification" not in arguments
        assert "reason" not in arguments


@pytest.mark.st_pi
def test_goal_plus_print_prompt_jsonl_has_typed_triage(
    st_pi_run_root: Path,
) -> None:
    search_root = st_pi_run_root / ".search"
    session_dir = st_pi_run_root / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_id = "st-pi-goal-plus-print"
    command = [
        *_pi_base_command(session_dir, session_id),
        "-p",
        f"/goal-plus {AUDIT_PROMPT}",
    ]

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=_run_env(search_root),
        text=True,
        capture_output=True,
        timeout=int(os.environ.get("ST_PI_TIMEOUT", "420")),
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:] or result.stdout[-2000:]
    record = _goal_record(search_root)
    assert record["status"] == "complete"
    events = _goal_events(search_root, record["goal_plus_id"])
    assert [event["event_type"] for event in events] == [
        "created",
        "triage_recorded",
        "status_changed",
    ]
    _assert_goal_plus_jsonl_is_clean(_session_file(session_dir))


@pytest.mark.st_pi
@pytest.mark.skipif(
    os.environ.get("ST_PI_TUI") != "1" or shutil.which("tmux") is None,
    reason="set ST_PI_TUI=1 and install tmux to run the interactive native /goal-plus stats smoke",
)
def test_goal_plus_native_tui_stats_entry_does_not_trigger_followup(
    st_pi_run_root: Path,
) -> None:
    search_root = st_pi_run_root / ".search"
    session_dir = st_pi_run_root / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_id = "st-pi-goal-plus-native"
    tmux_session = f"st-pi-{int(time.time())}"
    prompt = (
        "/goal-plus 请只读检查 README.md 是否存在。不要修改文件。"
        "最后把 Goal Plus 状态设为 complete。"
    )
    shell_command = shlex.join(
        [
            "env",
            f"AGENTIC_ANY_SEARCH_ROOT={search_root}",
            f"AGENTIC_ANY_SEARCH_SOURCE_PATH={ROOT}",
            *_pi_base_command(session_dir, session_id),
        ]
    )
    command = [
        "tmux",
        "new-session",
        "-d",
        "-s",
        tmux_session,
        "-x",
        "100",
        "-y",
        "30",
        shell_command,
    ]

    subprocess.run(command, cwd=ROOT, check=True)
    try:
        time.sleep(3)
        subprocess.run(["tmux", "send-keys", "-t", tmux_session, prompt, "Enter"], check=True)
        deadline = time.time() + int(os.environ.get("ST_PI_TIMEOUT", "420"))
        while time.time() < deadline:
            goal_files = sorted((search_root / "goal-plus").glob("gp_*/goal.json"))
            if goal_files:
                record = json.loads(goal_files[0].read_text(encoding="utf-8"))
                if record.get("status") == "complete":
                    break
            time.sleep(2)
        else:
            pane = subprocess.run(
                ["tmux", "capture-pane", "-t", tmux_session, "-p"],
                text=True,
                capture_output=True,
                check=False,
            )
            raise AssertionError(pane.stdout[-2000:])

        time.sleep(8)
    finally:
        subprocess.run(["tmux", "kill-session", "-t", tmux_session], check=False)

    session_path = _session_file(session_dir)
    _assert_goal_plus_jsonl_is_clean(session_path)
    entries = _read_jsonl(session_path)
    stats_indexes = [
        index
        for index, entry in enumerate(entries)
        if entry.get("type") == "custom"
        and entry.get("customType") == "goal-plus-stats"
    ]
    assert stats_indexes
    assert not any(
        (entry.get("message") or {}).get("role") == "assistant"
        for entry in entries[stats_indexes[-1] + 1 :]
    )
