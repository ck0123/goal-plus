from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from goal_plus import pi_worker


pytestmark = pytest.mark.pi


def _assistant_usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cost_total: float = 0.0,
) -> dict[str, Any]:
    return {
        "type": "message",
        "message": {
            "role": "assistant",
            "usage": {
                "input": input_tokens,
                "output": output_tokens,
                "cacheRead": cache_read,
                "cacheWrite": 0,
                "cost": {"total": cost_total},
            },
        },
    }


def test_summarize_pi_entries_reports_totals_and_latest_cache_hit_rate() -> None:
    assert hasattr(pi_worker, "summarize_pi_entries")
    summary = pi_worker.summarize_pi_entries(
        [
            _assistant_usage(
                input_tokens=100,
                output_tokens=10,
                cache_read=50,
                cost_total=0.01,
            ),
            {"type": "message", "message": {"role": "user"}},
            _assistant_usage(
                input_tokens=25,
                output_tokens=5,
                cache_read=75,
                cost_total=0.02,
            ),
        ]
    )

    assert summary == {
        "assistantMessages": 2,
        "input": 125,
        "output": 15,
        "cacheRead": 125,
        "cacheWrite": 0,
        "costTotal": 0.03,
        "latestCacheHitRate": 75.0,
    }


def test_rpc_client_compacts_stream_updates_and_skips_text_log_by_default(
    tmp_path: Path,
) -> None:
    event_log = tmp_path / "events.jsonl"
    client = pi_worker._RpcClient(  # type: ignore[arg-type]
        proc=object(),
        event_log=event_log,
        text_log=None,
    )

    client._append_event({"type": "message_update", "delta": "partial"})
    client._append_event(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "SECRET_REASONING"}],
                "usage": {"input": 10, "output": 2},
            },
        }
    )
    client._append_event(
        {
            "type": "tool_execution_end",
            "toolCallId": "call_1",
            "toolName": "search_get_agent_context",
            "isError": False,
            "result": {"content": [{"type": "text", "text": "SECRET_TOOL_RESULT"}]},
        }
    )
    client._append_event(
        {
            "type": "response",
            "id": "cmd_1",
            "command": "get_entries",
            "success": True,
            "data": {"entries": [{"message": "SECRET_TRANSCRIPT"}]},
        }
    )
    client._append_text('{"type":"message_update"}\n')

    log_text = event_log.read_text(encoding="utf-8")
    events = [json.loads(line) for line in log_text.splitlines()]
    assert [event["type"] for event in events] == [
        "message_end",
        "tool_execution_end",
        "response",
    ]
    assert events[0]["role"] == "assistant"
    assert events[0]["content_types"] == ["thinking"]
    assert events[1]["tool_name"] == "search_get_agent_context"
    assert events[2]["command"] == "get_entries"
    assert events[2]["entry_count"] == 1
    assert "SECRET" not in log_text
    assert not (tmp_path / "events.txt").exists()


def test_rpc_client_raw_logging_keeps_stream_updates_and_text(
    tmp_path: Path,
) -> None:
    event_log = tmp_path / "events.jsonl"
    text_log = tmp_path / "events.txt"
    client = pi_worker._RpcClient(  # type: ignore[arg-type]
        proc=object(),
        event_log=event_log,
        text_log=text_log,
        raw_logging=True,
    )

    client._append_event({"type": "message_update", "delta": "partial"})
    client._append_text('{"type":"message_update"}\n')

    assert '"type": "message_update"' in event_log.read_text(encoding="utf-8")
    assert text_log.read_text(encoding="utf-8") == '{"type":"message_update"}\n'


def test_run_pi_rpc_worker_returns_run_delta_metrics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    extension = tmp_path / "goal-plus.ts"
    extension.write_text("// fake extension\n", encoding="utf-8")
    session_dir = tmp_path / "sessions"
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    baseline_entries = [
        _assistant_usage(input_tokens=100, output_tokens=10, cache_read=50, cost_total=0.01)
    ]
    final_entries = [
        *baseline_entries,
        _assistant_usage(input_tokens=25, output_tokens=5, cache_read=75, cost_total=0.02),
    ]
    commands: list[str] = []
    popen_cmd: list[str] = []
    popen_env: dict[str, str] = {}

    class FakeProc:
        returncode = None

    class FakeRpcClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.entries_calls = 0

        def start(self) -> None:
            return None

        def command(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
            command_type = str(payload["type"])
            commands.append(command_type)
            if command_type == "get_entries":
                self.entries_calls += 1
                entries = baseline_entries if self.entries_calls == 1 else final_entries
                return {"data": {"entries": entries, "leafId": None}}
            if command_type == "prompt":
                return {"data": {}}
            if command_type == "set_thinking_level":
                return {"data": {}}
            if command_type == "get_state":
                return {
                    "data": {
                        "isStreaming": False,
                        "isCompacting": False,
                        "pendingMessageCount": 0,
                        "sessionFile": str(session_dir / "2026_agent_1.jsonl"),
                    }
                }
            if command_type == "get_last_assistant_text":
                return {"data": {"text": "done"}}
            if command_type == "get_session_stats":
                return {"data": {"tokens": {"input": 125}}}
            raise AssertionError(f"unexpected command {command_type}")

    def fake_popen(cmd: list[str], *_args: Any, **kwargs: Any) -> FakeProc:
        popen_cmd[:] = [str(part) for part in cmd]
        popen_env.update(kwargs.get("env") or {})
        return FakeProc()

    def fake_kill_process_group(proc: FakeProc) -> None:
        proc.returncode = 0

    monkeypatch.setattr(pi_worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(pi_worker, "_RpcClient", FakeRpcClient)
    monkeypatch.setattr(pi_worker, "_kill_process_group", fake_kill_process_group)

    handle = pi_worker.run_pi_rpc_worker(
        {
            "agent_session_id": "agent_1",
            "session_id": "agent_1",
            "session_dir": str(session_dir),
            "root": str(tmp_path / ".search"),
            "cwd": str(cwd),
            "prompt": "do work",
            "budget_control": {"max_runtime_seconds": 30},
            "model_pattern": "gpt-5.4-mini",
            "thinking_level": "high",
        },
        extension_path=extension,
    )

    metrics = handle["metadata"]["pi_metrics"]
    assert metrics["session_file"] == str(session_dir / "2026_agent_1.jsonl")
    assert metrics["model"] == "gpt-5.4-mini"
    assert metrics["thinking_level"] == "high"
    assert metrics["baseline_entry_count"] == 1
    assert metrics["final_entry_count"] == 2
    assert metrics["duration_seconds"] >= 0
    assert metrics["usage_delta"] == {
        "assistantMessages": 1,
        "input": 25,
        "output": 5,
        "cacheRead": 75,
        "cacheWrite": 0,
        "costTotal": 0.02,
        "latestCacheHitRate": 75.0,
    }
    assert metrics["usage_total"] == {
        "assistantMessages": 2,
        "input": 125,
        "output": 15,
        "cacheRead": 125,
        "cacheWrite": 0,
        "costTotal": 0.03,
        "latestCacheHitRate": 75.0,
    }
    assert metrics["session_stats"] == {"tokens": {"input": 125}}
    assert commands.count("get_entries") == 2
    assert "set_thinking_level" in commands
    assert popen_cmd[0:3] == ["pi", "--model", "gpt-5.4-mini"]
    assert "--no-session" in popen_cmd
    assert "--session-dir" not in popen_cmd
    assert popen_env["GOAL_PLUS_SOURCE_PATH"] == str(pi_worker.default_extension_path().parents[2])
    assert popen_env["GOAL_PLUS_PI_ROLE"] == "worker"
    assert handle["metadata"]["raw_logging"] is False
    assert handle["metadata"]["text_log"] is None
    assert handle["metadata"]["session_file"] == str(session_dir / "2026_agent_1.jsonl")
    assert handle["metadata"]["continuation"] == "state_redispatch"
    assert handle["metadata"]["progress_handoff"]["status"] == "completed"
    assert handle["metadata"]["progress_handoff"]["summary"] == "done"

    popen_env.clear()
    pi_worker.run_pi_rpc_worker(
        {
            "role": "final-checker",
            "agent_session_id": "fc_1",
            "session_id": "fc_1",
            "root": str(tmp_path / ".search"),
            "cwd": str(cwd),
            "prompt": "audit and submit",
            "budget_control": {"max_runtime_seconds": 30},
        },
        extension_path=extension,
    )
    assert popen_env["GOAL_PLUS_PI_ROLE"] == "final-checker"


def test_workspace_progress_handoff_preserves_model_and_git_progress(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "initial_program.py").write_text("VALUE = 0\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "initial_program.py"], cwd=workspace, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-q",
            "-m",
            "baseline",
        ],
        cwd=workspace,
        check=True,
    )
    (workspace / "initial_program.py").write_text("VALUE = 7\n", encoding="utf-8")
    (workspace / "__pycache__").mkdir()
    (workspace / "__pycache__" / "initial_program.cpython-311.pyc").write_bytes(b"cache")
    (workspace / ".tmp").mkdir()
    (workspace / ".tmp" / "handoff.json").write_text(
        json.dumps(
            {
                "summary": "implemented the first half",
                "what_was_tried": ["changed VALUE"],
                "next_steps": ["run verifier"],
            }
        ),
        encoding="utf-8",
    )

    handoff = pi_worker._workspace_progress_handoff(
        workspace,
        root=tmp_path / ".search",
        run_id="run_missing",
        candidate_id="c001",
        timed_out=True,
        runner_failed=False,
        assistant_text=None,
    )

    assert handoff["status"] == "timed_out"
    assert handoff["summary"] == "implemented the first half"
    assert handoff["model_handoff"]["next_steps"] == ["run verifier"]
    assert handoff["workspace"]["dirty"] is True
    assert handoff["workspace"]["changed_files"] == ["initial_program.py"]
    assert "initial_program.py" in handoff["workspace"]["diff_stat"]
    assert handoff["verifier"]["count"] == 0


def test_verifier_snapshot_respects_minimize_direction(tmp_path: Path) -> None:
    root = tmp_path / ".search"
    candidate_dir = root / "runs" / "run_1" / "candidates" / "c001"
    candidate_dir.mkdir(parents=True)
    (root / "runs" / "run_1" / "run.json").write_text(
        json.dumps({"frozen_spec_id": "spec_1"}), encoding="utf-8"
    )
    spec_dir = root / "specs" / "spec_1"
    spec_dir.mkdir(parents=True)
    (spec_dir / "frozen_spec.json").write_text(
        json.dumps({"spec": {"metric_direction": "minimize"}}), encoding="utf-8"
    )
    (candidate_dir / "candidate.json").write_text(
        json.dumps(
            {
                "iterations": [
                    {"iteration": 1, "score": 1.0, "process_passed": True},
                    {"iteration": 2, "score": 5.0, "process_passed": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    snapshot = pi_worker._verifier_snapshot(root, "run_1", "c001")

    assert snapshot["best_iteration"] == 1
    assert snapshot["best_score"] == 1.0


def test_run_pi_rpc_worker_waits_for_pi_auto_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    extension = tmp_path / "goal-plus.ts"
    extension.write_text("// fake extension\n", encoding="utf-8")
    session_dir = tmp_path / "sessions"
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    commands: list[str] = []
    states = [
        {"isStreaming": False, "isCompacting": False, "pendingMessageCount": 0},
        {"isStreaming": True, "isCompacting": False, "pendingMessageCount": 0},
        {
            "isStreaming": False,
            "isCompacting": False,
            "pendingMessageCount": 0,
            "sessionFile": str(session_dir / "2026_agent_retry.jsonl"),
        },
    ]

    class FakeProc:
        returncode = None

    class FakeRpcClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.retry_checks = 0

        def start(self) -> None:
            return None

        def auto_retry_pending(self) -> bool:
            self.retry_checks += 1
            return self.retry_checks == 1

        def command(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
            command_type = str(payload["type"])
            commands.append(command_type)
            if command_type == "get_entries":
                return {"data": {"entries": [], "leafId": None}}
            if command_type == "prompt":
                return {"data": {}}
            if command_type == "get_state":
                return {"data": states.pop(0)}
            if command_type == "get_last_assistant_text":
                return {"data": {"text": "done after retry"}}
            if command_type == "get_session_stats":
                return {"data": {}}
            raise AssertionError(f"unexpected command {command_type}")

    def fake_popen(*_args: Any, **_kwargs: Any) -> FakeProc:
        return FakeProc()

    def fake_kill_process_group(proc: FakeProc) -> None:
        proc.returncode = 0

    monkeypatch.setattr(pi_worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(pi_worker, "_RpcClient", FakeRpcClient)
    monkeypatch.setattr(pi_worker, "_kill_process_group", fake_kill_process_group)
    monkeypatch.setattr(pi_worker.time, "sleep", lambda _seconds: None)

    handle = pi_worker.run_pi_rpc_worker(
        {
            "agent_session_id": "agent_retry",
            "session_id": "agent_retry",
            "session_dir": str(session_dir),
            "root": str(tmp_path / ".search"),
            "cwd": str(cwd),
            "prompt": "do work",
            "budget_control": {"max_runtime_seconds": 30},
        },
        extension_path=extension,
    )

    assert handle["metadata"]["assistant_text"] == "done after retry"
    assert commands.count("get_state") >= 3


def test_run_pi_rpc_worker_steers_once_before_hard_deadline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    extension = tmp_path / "goal-plus.ts"
    extension.write_text("// fake extension\n", encoding="utf-8")
    session_dir = tmp_path / "sessions"
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    commands: list[dict[str, Any]] = []
    clock = {"value": 0.0}

    class FakeProc:
        returncode = None

    class FakeRpcClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.state_calls = 0

        def start(self) -> None:
            return None

        def command(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
            commands.append(payload)
            command_type = str(payload["type"])
            if command_type == "get_entries":
                return {"data": {"entries": [], "leafId": None}}
            if command_type in {"prompt", "steer"}:
                return {"data": {}}
            if command_type == "get_state":
                self.state_calls += 1
                if self.state_calls == 1:
                    clock["value"] = 25.0
                    return {
                        "data": {
                            "isStreaming": True,
                            "isCompacting": False,
                            "pendingMessageCount": 0,
                        }
                    }
                return {
                    "data": {
                        "isStreaming": False,
                        "isCompacting": False,
                        "pendingMessageCount": 0,
                    }
                }
            if command_type == "get_last_assistant_text":
                return {"data": {"text": "closed out"}}
            if command_type == "get_session_stats":
                return {"data": {}}
            raise AssertionError(f"unexpected command {command_type}")

    def fake_popen(*_args: Any, **_kwargs: Any) -> FakeProc:
        return FakeProc()

    def fake_kill_process_group(proc: FakeProc) -> None:
        proc.returncode = 0

    def fake_sleep(seconds: float) -> None:
        clock["value"] += seconds

    monkeypatch.setattr(pi_worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(pi_worker, "_RpcClient", FakeRpcClient)
    monkeypatch.setattr(pi_worker, "_kill_process_group", fake_kill_process_group)
    monkeypatch.setattr(pi_worker.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(pi_worker.time, "sleep", fake_sleep)

    handle = pi_worker.run_pi_rpc_worker(
        {
            "agent_session_id": "agent_closeout",
            "session_id": "agent_closeout",
            "session_dir": str(session_dir),
            "root": str(tmp_path / ".search"),
            "cwd": str(cwd),
            "prompt": "do work",
            "budget_control": {
                "max_runtime_seconds": 30,
                "soft_closeout_seconds": 6,
            },
        },
        extension_path=extension,
    )

    steer_commands = [command for command in commands if command["type"] == "steer"]
    assert len(steer_commands) == 1
    assert "final search_run_verifier" in steer_commands[0]["message"]
    assert handle["metadata"]["soft_closeout_seconds"] == 6
    assert handle["metadata"]["soft_closeout_sent"] is True
    assert handle["metadata"]["timed_out"] is False


@pytest.mark.parametrize(
    ("role", "expected_sent"),
    [("worker", True), ("final-checker", False)],
)
def test_run_pi_rpc_worker_checks_time_advisory_after_worker_tool_only(
    monkeypatch,
    tmp_path: Path,
    role: str,
    expected_sent: bool,
) -> None:
    extension = tmp_path / "goal-plus.ts"
    extension.write_text("// fake extension\n", encoding="utf-8")
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    commands: list[dict[str, Any]] = []
    advisory_calls: list[dict[str, Any]] = []

    class FakeProc:
        returncode = None

    class FakeRpcClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.state_calls = 0
            self.drained = False

        def start(self) -> None:
            return None

        def drain_completed_tools(self) -> list[str]:
            if self.drained:
                return []
            self.drained = True
            return ["search_run_verifier"]

        def auto_retry_pending(self) -> bool:
            return False

        def command(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
            commands.append(payload)
            command_type = str(payload["type"])
            if command_type == "get_entries":
                return {"data": {"entries": [], "leafId": None}}
            if command_type in {"prompt", "steer"}:
                return {"data": {}}
            if command_type == "get_state":
                self.state_calls += 1
                return {
                    "data": {
                        "isStreaming": self.state_calls == 1,
                        "isCompacting": False,
                        "pendingMessageCount": 0,
                    }
                }
            if command_type == "get_last_assistant_text":
                return {"data": {"text": "done"}}
            if command_type == "get_session_stats":
                return {"data": {}}
            raise AssertionError(f"unexpected command {command_type}")

    def fake_advisory(
        root: Path,
        agent_session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        advisory_calls.append(
            {"root": root, "agent_session_id": agent_session_id, **kwargs}
        )
        return {
            "run_id": "run_1",
            "candidate_id": "c001",
            "agent_session_id": agent_session_id,
            "remaining_seconds": 5.0,
            "average_submission_seconds": 20.0,
            "total_verifier_count": 1,
            "low_sample": True,
            "candidates": [],
            "message": "dynamic time advisory",
        }

    def fake_popen(*_args: Any, **_kwargs: Any) -> FakeProc:
        return FakeProc()

    def fake_kill_process_group(proc: FakeProc) -> None:
        proc.returncode = 0

    monkeypatch.setattr(pi_worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(pi_worker, "_RpcClient", FakeRpcClient)
    monkeypatch.setattr(pi_worker, "_kill_process_group", fake_kill_process_group)
    monkeypatch.setattr(pi_worker, "build_search_time_advisory", fake_advisory)
    monkeypatch.setattr(pi_worker.time, "sleep", lambda _seconds: None)

    handle = pi_worker.run_pi_rpc_worker(
        {
            "agent_session_id": "agent_time",
            "candidate_id": "c001",
            "run_id": "run_1",
            "session_id": "agent_time",
            "root": str(tmp_path / ".gp"),
            "cwd": str(cwd),
            "prompt": "do work",
            "role": role,
            "budget_control": {"max_runtime_seconds": 60},
        },
        extension_path=extension,
    )

    dynamic_steers = [
        command
        for command in commands
        if command["type"] == "steer"
        and command.get("message") == "dynamic time advisory"
    ]
    assert bool(advisory_calls) is expected_sent
    assert bool(dynamic_steers) is expected_sent
    assert handle["metadata"]["time_advisory_sent"] is expected_sent
    if expected_sent:
        assert handle["metadata"]["time_advisory"]["trigger_tool"] == (
            "search_run_verifier"
        )
    else:
        assert handle["metadata"]["time_advisory"] is None
