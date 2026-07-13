from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from goal_plus.monitor import goal_plus_monitor_snapshot


ROOT = Path(__file__).resolve().parents[2]
INSTALL_HINT = (
    'Install this project into the Python environment that launches Pi: '
    'python -m pip install -e ".[dev]".'
)
AUDIT_PROMPT = (
    "请只读审计当前分支的 Pi native Goal Plus 改造是否完整。范围限定为 "
    ".pi/extensions/goal-plus.ts、.pi/skills/goal-plus/SKILL.md、docs/pi.md、"
    "tests/test_pi_assets.py。重点检查：/goal-plus 命令、session_start 状态恢复、"
    "before_agent_start 上下文注入、tool_call pre-tool gate、agent_end stop gate、"
    "terminal stats 输出。不要修改文件。最后给出结论，并把 Goal Plus 状态设为 complete。"
)
AUTONOMOUS_OPTIMIZATION_PROMPT = (
    "优化 examples/edgebench-ad-placement/workspace 中的广告矩形布局程序："
    "在保持所有公开案例输出合法的前提下，尽量提高综合得分。"
    "先自行检查工作区，找出可重复的评估方法、优化指标和安全的修改边界，"
    "再由 Goal Plus 自主选择合适的执行方式，不要等待额外的用户确认。"
    "这是一个低成本冒烟验证：最多探索 1 个候选，并行度为 1，单个候选最多运行 "
    "60 秒和 4 轮。只允许修改 initial_program.py，不要修改工作区中的其他文件。"
    "完成后应用最佳的可验证结果、报告最终得分，并把 Goal Plus 状态设为 complete。"
)
MULTI_SEARCH_PROMPT = (
    "在同一个 Goal Plus 任务里顺序运行两个最小但完整的 Pi search task。两次都读取 "
    "examples/edgebench_ad_placement_search_spec.json，并使用 "
    "examples/edgebench-ad-placement/workspace/evaluator.py 作为冻结 verifier。"
    "第一个 SearchSpec 使用 strategy.name=random，第二个使用 strategy.name=agent_guided；"
    "两次都设置 worker_host=pi-rpc、worker_mode=agent-session-pool、max_candidates=1、"
    "max_parallel=1，worker budget 为 max_runtime_seconds=60、max_turns=4、"
    "on_exceed=interrupt，只允许候选修改 initial_program.py。每次都必须分别完成 "
    "search_freeze_spec、search_create、goal_plus_link_search_run、candidate verifier、"
    "selection、report、promotion 和 goal_plus_record_search_result。第一次结果记录后不要"
    "结束 Goal Plus，继续创建并链接第二个不同 run_id。只有两个 search task 都完成后，"
    "才把 Goal Plus 状态设为 complete。"
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
        "GOAL_PLUS_ROOT": str(search_root),
        "GOAL_PLUS_SOURCE_PATH": str(ROOT),
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
    search_root = st_pi_run_root / ".gp"
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
    event_types = [event["event_type"] for event in events]
    assert event_types[:2] == ["created", "triage_recorded"]
    assert "status_changed" in event_types
    assert "spec_draft_saved" not in event_types
    assert "search_linked" not in event_types
    triage = next(
        event["payload"]
        for event in events
        if event["event_type"] == "triage_recorded"
    )
    assert triage["is_optimization"] is False
    assert triage["recommended_phase"] == "goal"
    _assert_goal_plus_jsonl_is_clean(_session_file(session_dir))


@pytest.mark.st_pi
def test_goal_plus_with_final_check_runs_pi_reviewer(
    st_pi_run_root: Path,
) -> None:
    search_root = st_pi_run_root / ".gp"
    session_dir = st_pi_run_root / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    command = [
        *_pi_base_command(session_dir, "st-pi-goal-plus-final-check"),
        "-p",
        (
            "/goal-plus-with-final-check Read pyproject.toml and prove that the project "
            "name is goal-plus. Do not edit any file. Use Goal Mode and finish only through "
            "the independent Pi final reviewer."
        ),
    ]

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=_run_env(search_root),
        text=True,
        capture_output=True,
        timeout=int(os.environ.get("ST_PI_FINAL_CHECK_TIMEOUT", "600")),
        check=False,
    )

    assert result.returncode == 0, result.stderr[-3000:] or result.stdout[-3000:]
    record = _goal_record(search_root)
    assert record["status"] == "complete"
    assert record["policy"]["final_check"]["mode"] == "required"
    assert record["goal_revision"] == 1
    assert record["final_checks"][-1]["checker_host"] == "pi"
    assert record["final_checks"][-1]["status"] == "passed"
    event_types = [
        event["event_type"]
        for event in _goal_events(search_root, record["goal_plus_id"])
    ]
    assert "final_check_requested" in event_types
    assert "final_check_submitted" in event_types


@pytest.mark.st_pi
def test_goal_plus_print_autonomously_enters_search(
    st_pi_run_root: Path,
) -> None:
    search_root = st_pi_run_root / ".gp"
    session_dir = st_pi_run_root / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    command = [
        *_pi_base_command(session_dir, "st-pi-goal-plus-autonomous-search"),
        "-p",
        f"/goal-plus {AUTONOMOUS_OPTIMIZATION_PROMPT}",
    ]

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=_run_env(search_root),
        text=True,
        capture_output=True,
        timeout=int(os.environ.get("ST_PI_SEARCH_TIMEOUT", "600")),
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:] or result.stdout[-2000:]
    record = _goal_record(search_root)
    assert record["status"] == "complete"
    events = _goal_events(search_root, record["goal_plus_id"])
    event_types = [event["event_type"] for event in events]
    assert "triage_recorded" in event_types
    assert "spec_draft_saved" in event_types
    assert "frozen_verifier_confirmed" not in event_types
    assert "search_linked" in event_types
    triage = next(
        event["payload"]
        for event in events
        if event["event_type"] == "triage_recorded"
    )
    assert triage["is_optimization"] is True
    assert triage["recommended_phase"] in {"spec_discovery", "search"}
    draft = next(
        event["payload"]
        for event in events
        if event["event_type"] == "spec_draft_saved"
    )
    assert draft["confidence"] == "high"
    assert draft.get("open_questions") == []
    assert draft.get("user_confirmed_frozen_verifier") is False
    linked = record.get("linked_search") or {}
    assert linked.get("run_id"), record
    assert linked.get("selected_candidate_id"), record
    assert linked.get("report_path"), record
    assert linked.get("promotion_artifact_path"), record
    assert Path(linked["report_path"]).exists()
    assert Path(linked["promotion_artifact_path"]).exists()
    run_record = json.loads(
        (search_root / "runs" / linked["run_id"] / "run.json").read_text(encoding="utf-8")
    )
    assert run_record["state"] == "promoted"
    worker_sessions = sorted(
        (search_root / "runs" / linked["run_id"] / "agent_sessions").glob("agent_*.json")
    )
    assert worker_sessions
    for session_path in worker_sessions:
        session = json.loads(session_path.read_text(encoding="utf-8"))
        metadata = session["host_handle"]["metadata"]
        assert metadata["continuation"] == "state_redispatch"
        assert metadata.get("runner_failed") is not True
        assert metadata["raw_logging"] is False
        assert metadata["session_file"] is None
        assert metadata["text_log"] is None
        assert Path(metadata["event_log"]).exists()
    assert not (search_root / "host-logs" / "pi-rpc-sessions").exists()
    _assert_goal_plus_jsonl_is_clean(_session_file(session_dir))


@pytest.mark.st_pi
def test_goal_plus_print_supports_two_search_tasks_in_one_goal(
    st_pi_run_root: Path,
) -> None:
    search_root = st_pi_run_root / ".gp"
    session_dir = st_pi_run_root / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    command = [
        *_pi_base_command(session_dir, "st-pi-goal-plus-multi-search"),
        "-p",
        f"/goal-plus {MULTI_SEARCH_PROMPT}",
    ]

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=_run_env(search_root),
        text=True,
        capture_output=True,
        timeout=int(os.environ.get("ST_PI_MULTI_SEARCH_TIMEOUT", "900")),
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:] or result.stdout[-2000:]
    record = _goal_record(search_root)
    assert record["status"] == "complete"
    search_tasks = record.get("search_tasks") or []
    assert len(search_tasks) == 2, record
    run_ids = [task.get("run_id") for task in search_tasks]
    assert all(run_ids)
    assert len(set(run_ids)) == 2
    for task in search_tasks:
        assert task.get("selected_candidate_id"), task
        assert task.get("report_path"), task
        assert task.get("promotion_artifact_path"), task
        assert Path(task["report_path"]).exists()
        assert Path(task["promotion_artifact_path"]).exists()
        run_record = json.loads(
            (search_root / "runs" / task["run_id"] / "run.json").read_text(
                encoding="utf-8"
            )
        )
        assert run_record["state"] == "promoted"

    snapshot = goal_plus_monitor_snapshot(
        root_dir=search_root,
        goal_plus_id=record["goal_plus_id"],
    )
    assert snapshot["goal_plus"]["search_tasks_total"] == 2
    assert snapshot["search_task_aggregate"]["search_tasks_total"] == 2
    assert snapshot["search_task_aggregate"]["started_rounds_total"] == 2
    assert [task["strategy"]["name"] for task in snapshot["search_tasks"]] == [
        "random",
        "agent_guided",
    ]
    assert not any(
        warning["kind"] in {
            "linked_search_run_missing",
            "linked_search_spec_mismatch",
            "linked_search_frozen_spec_missing",
            "completed_goal_current_search_not_promoted",
        }
        for warning in snapshot["warnings"]
    )
    _assert_goal_plus_jsonl_is_clean(_session_file(session_dir))


@pytest.mark.st_pi
@pytest.mark.skipif(
    os.environ.get("ST_PI_TUI") != "1" or shutil.which("tmux") is None,
    reason="set ST_PI_TUI=1 and install tmux to run the interactive native /goal-plus stats smoke",
)
def test_goal_plus_native_tui_stats_entry_does_not_trigger_followup(
    st_pi_run_root: Path,
) -> None:
    search_root = st_pi_run_root / ".gp"
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
            f"GOAL_PLUS_ROOT={search_root}",
            f"GOAL_PLUS_SOURCE_PATH={ROOT}",
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
