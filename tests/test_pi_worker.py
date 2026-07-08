from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_any_search_mcp import pi_worker


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


def test_run_pi_rpc_worker_returns_run_delta_metrics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    extension = tmp_path / "search-runtime.ts"
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
        },
        extension_path=extension,
        model_pattern="gpt-5.4-mini",
    )

    metrics = handle["metadata"]["pi_metrics"]
    assert metrics["session_file"] == str(session_dir / "2026_agent_1.jsonl")
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
    assert popen_cmd[0:3] == ["pi", "--model", "gpt-5.4-mini"]
    assert popen_env["AGENTIC_ANY_SEARCH_SOURCE_PATH"] == str(pi_worker.default_extension_path().parents[2])


def test_run_pi_rpc_worker_waits_for_pi_auto_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    extension = tmp_path / "search-runtime.ts"
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
