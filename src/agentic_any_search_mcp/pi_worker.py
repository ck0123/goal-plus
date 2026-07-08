from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PiRpcError(RuntimeError):
    pass


class _RpcClient:
    def __init__(
        self,
        *,
        proc: subprocess.Popen[str],
        event_log: Path,
        text_log: Path,
    ) -> None:
        self.proc = proc
        self.event_log = event_log
        self.text_log = text_log
        self._condition = threading.Condition()
        self._responses: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._auto_retry_until = 0.0
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)

    def start(self) -> None:
        self.event_log.parent.mkdir(parents=True, exist_ok=True)
        self.text_log.parent.mkdir(parents=True, exist_ok=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def command(
        self,
        payload: dict[str, Any],
        *,
        timeout: float,
        wait_for_response: bool = True,
    ) -> dict[str, Any] | None:
        self._counter += 1
        command_id = payload.get("id") or f"cmd_{self._counter:04d}"
        payload = {**payload, "id": command_id}
        if self.proc.stdin is None:
            raise PiRpcError("Pi RPC stdin is closed")
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        if not wait_for_response:
            return None
        return self._wait_response(command_id, timeout=timeout)

    def _wait_response(self, command_id: str, *, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while command_id not in self._responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"timed out waiting for Pi RPC response {command_id}")
                self._condition.wait(remaining)
            response = self._responses.pop(command_id)
        if not response.get("success", False):
            raise PiRpcError(str(response.get("error") or response))
        return response

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            text = line.rstrip("\n")
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                event = {"type": "raw_stdout", "text": text}
            self._append_event(event)
            self._append_text(line)
            if event.get("type") == "response" and event.get("id"):
                with self._condition:
                    self._responses[str(event["id"])] = event
                    self._condition.notify_all()
            if event.get("type") == "auto_retry_start":
                delay_seconds = _number(event.get("delayMs")) / 1000
                with self._condition:
                    self._auto_retry_until = max(
                        self._auto_retry_until,
                        time.monotonic() + max(float(delay_seconds), 1.0),
                    )
                    self._condition.notify_all()
            if event.get("type") == "agent_start":
                with self._condition:
                    self._auto_retry_until = 0.0
                    self._condition.notify_all()

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._append_event({"type": "stderr", "text": line.rstrip("\n")})
            self._append_text(line)

    def _append_event(self, event: dict[str, Any]) -> None:
        with self.event_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _append_text(self, line: str) -> None:
        with self.text_log.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def auto_retry_pending(self) -> bool:
        with self._condition:
            return time.monotonic() < self._auto_retry_until


def default_extension_path() -> Path:
    source_root = Path(__file__).resolve().parents[2]
    return source_root / ".pi" / "extensions" / "search-runtime.ts"


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait(timeout=5)


def _safe_session_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> int | float:
    return value if isinstance(value, int | float) else 0


def summarize_pi_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "assistantMessages": 0,
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "costTotal": 0.0,
        "latestCacheHitRate": None,
    }
    for entry in entries:
        message = entry.get("message") or {}
        if message.get("role") != "assistant":
            continue
        usage = message.get("usage") or {}
        input_tokens = int(_number(usage.get("input")))
        output_tokens = int(_number(usage.get("output")))
        cache_read = int(_number(usage.get("cacheRead")))
        cache_write = int(_number(usage.get("cacheWrite")))
        cost = usage.get("cost") or {}
        cost_total = _number(cost.get("total")) if isinstance(cost, dict) else 0

        summary["assistantMessages"] += 1
        summary["input"] += input_tokens
        summary["output"] += output_tokens
        summary["cacheRead"] += cache_read
        summary["cacheWrite"] += cache_write
        summary["costTotal"] += float(cost_total)

        prompt_tokens = input_tokens + cache_read + cache_write
        summary["latestCacheHitRate"] = (
            (cache_read / prompt_tokens) * 100 if prompt_tokens else None
        )

    summary["costTotal"] = round(float(summary["costTotal"]), 12)
    return summary


def _rpc_data(
    rpc: Any,
    payload: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    response = rpc.command(payload, timeout=timeout)
    return dict((response or {}).get("data") or {})


def _rpc_entries(rpc: Any, *, timeout: float) -> list[dict[str, Any]]:
    data = _rpc_data(rpc, {"type": "get_entries"}, timeout=timeout)
    entries = data.get("entries")
    return entries if isinstance(entries, list) else []


def _last_entry_id(entries: list[dict[str, Any]]) -> str | None:
    if not entries:
        return None
    value = entries[-1].get("id")
    return str(value) if value is not None else None


def _find_session_file(session_dir: Path, session_id: str) -> str | None:
    matches = sorted(
        session_dir.glob(f"*_{session_id}.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return str(matches[0]) if matches else None


def _collect_pi_metrics(
    rpc: Any,
    *,
    session_dir: Path,
    session_id: str,
    baseline_entries: list[dict[str, Any]],
    baseline_error: str | None,
    last_state_data: dict[str, Any],
    started_at: str,
    ended_at: str,
    duration_seconds: float,
) -> dict[str, Any]:
    errors: list[str] = []
    if baseline_error:
        errors.append(f"baseline_entries: {baseline_error}")

    state_data = dict(last_state_data)
    try:
        state_data.update(_rpc_data(rpc, {"type": "get_state"}, timeout=5))
    except Exception as exc:
        errors.append(f"get_state: {exc}")

    try:
        final_entries = _rpc_entries(rpc, timeout=10)
    except Exception as exc:
        errors.append(f"get_entries: {exc}")
        final_entries = []

    try:
        session_stats: dict[str, Any] | None = _rpc_data(
            rpc,
            {"type": "get_session_stats"},
            timeout=10,
        )
    except Exception as exc:
        errors.append(f"get_session_stats: {exc}")
        session_stats = None

    session_file = state_data.get("sessionFile") or _find_session_file(session_dir, session_id)
    baseline_count = len(baseline_entries)
    delta_entries = final_entries[baseline_count:] if len(final_entries) >= baseline_count else []
    scope = "run_delta" if not baseline_error and final_entries else "session_total_fallback"
    metrics: dict[str, Any] = {
        "scope": scope,
        "session_id": session_id,
        "session_file": session_file,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "baseline_entry_count": baseline_count,
        "final_entry_count": len(final_entries),
        "baseline_last_entry_id": _last_entry_id(baseline_entries),
        "final_last_entry_id": _last_entry_id(final_entries),
        "usage_delta": summarize_pi_entries(delta_entries),
        "usage_total": summarize_pi_entries(final_entries),
        "session_stats": session_stats,
    }
    if errors:
        metrics["errors"] = errors
    return metrics


def run_pi_rpc_worker(
    launch: dict[str, Any],
    *,
    pi_binary: str = "pi",
    extension_path: Path | str | None = None,
    thinking_level: str | None = None,
    model_pattern: str | None = None,
    provider: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    agent_session_id = str(launch["agent_session_id"])
    session_id = _safe_session_name(str(launch.get("session_id") or agent_session_id))
    root = Path(str(launch.get("root") or os.environ.get("AGENTIC_ANY_SEARCH_ROOT", ".search"))).resolve()
    cwd = Path(str(launch["cwd"])).resolve()
    budget = dict(launch.get("budget_control") or {})
    max_runtime_seconds = budget.get("max_runtime_seconds")
    if max_runtime_seconds is None:
        raise ValueError("pi_rpc_worker launch requires budget_control.max_runtime_seconds")
    timeout_seconds = int(max_runtime_seconds)

    host_logs = root / "host-logs"
    session_dir = Path(str(launch.get("session_dir") or host_logs / "pi-rpc-sessions")).resolve()
    event_log = host_logs / f"pi-rpc-{session_id}.jsonl"
    text_log = host_logs / f"pi-rpc-{session_id}.txt"
    extension = Path(extension_path) if extension_path else default_extension_path()
    if not extension.exists():
        raise FileNotFoundError(f"Pi extension not found: {extension}")

    session_dir.mkdir(parents=True, exist_ok=True)
    host_logs.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "AGENTIC_ANY_SEARCH_ROOT": str(root),
        "AGENTIC_ANY_SEARCH_PI_ROLE": "worker",
        "AGENTIC_ANY_SEARCH_SOURCE_PATH": str(default_extension_path().parents[2]),
    }
    selected_model_pattern = model_pattern or os.environ.get("AGENTIC_ANY_SEARCH_PI_MODEL")
    cmd = [pi_binary]
    if selected_model_pattern:
        cmd.extend(["--model", selected_model_pattern])
    cmd.extend(
        [
            "--mode",
            "rpc",
            "--approve",
            "--session-dir",
            str(session_dir),
            "--session-id",
            session_id,
            "-e",
            str(extension),
        ]
    )
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    rpc = _RpcClient(proc=proc, event_log=event_log, text_log=text_log)
    rpc.start()

    assistant_text: str | None = None
    timed_out = False
    started_at = _utc_timestamp()
    started_monotonic = time.monotonic()
    deadline = started_monotonic + timeout_seconds
    baseline_entries: list[dict[str, Any]] = []
    baseline_error: str | None = None
    last_state_data: dict[str, Any] = {}
    pi_metrics: dict[str, Any] | None = None

    def _abort_for_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        try:
            rpc.command({"type": "abort"}, timeout=5)
        except Exception:
            pass

    try:
        if provider and model_id:
            rpc.command(
                {"type": "set_model", "provider": provider, "modelId": model_id},
                timeout=min(30, timeout_seconds),
            )
        selected_thinking = thinking_level or launch.get("thinking_level")
        if selected_thinking:
            rpc.command(
                {"type": "set_thinking_level", "level": selected_thinking},
                timeout=min(30, timeout_seconds),
            )
        try:
            baseline_entries = _rpc_entries(rpc, timeout=min(10, timeout_seconds))
        except Exception as exc:
            baseline_error = str(exc)
        rpc.command(
            {"type": "prompt", "message": str(launch["prompt"])},
            timeout=min(60, timeout_seconds),
        )

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _abort_for_timeout()
                break
            state = rpc.command({"type": "get_state"}, timeout=min(10, remaining))
            data = dict(state.get("data") or {})
            last_state_data = data
            if (
                not data.get("isStreaming", False)
                and not data.get("isCompacting", False)
                and int(data.get("pendingMessageCount") or 0) == 0
            ):
                auto_retry_pending = getattr(rpc, "auto_retry_pending", lambda: False)
                if auto_retry_pending():
                    time.sleep(min(1.0, max(0.1, remaining)))
                    continue
                break
            time.sleep(min(1.0, max(0.1, remaining)))

    except TimeoutError:
        _abort_for_timeout()
    else:
        if not timed_out:
            try:
                response = rpc.command({"type": "get_last_assistant_text"}, timeout=10)
                assistant_text = (response.get("data") or {}).get("text")
            except TimeoutError:
                assistant_text = None
    finally:
        ended_at = _utc_timestamp()
        duration_seconds = time.monotonic() - started_monotonic
        try:
            pi_metrics = _collect_pi_metrics(
                rpc,
                session_dir=session_dir,
                session_id=session_id,
                baseline_entries=baseline_entries,
                baseline_error=baseline_error,
                last_state_data=last_state_data,
                started_at=started_at,
                ended_at=ended_at,
                duration_seconds=duration_seconds,
            )
        except Exception as exc:
            pi_metrics = {
                "scope": "unavailable",
                "session_id": session_id,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_seconds": duration_seconds,
                "errors": [str(exc)],
            }
        _kill_process_group(proc)

    return {
        "host": "pi-rpc",
        "external_id": session_id,
        "metadata": {
            "session_dir": str(session_dir),
            "event_log": str(event_log),
            "text_log": str(text_log),
            "session_file": pi_metrics.get("session_file") if pi_metrics else None,
            "pi_metrics": pi_metrics,
            "assistant_text": assistant_text,
            "timed_out": timed_out,
            "exit_code": proc.returncode,
            "continuation": "session_jsonl_restart",
        },
    }


def _read_launch(args_json: str | None) -> dict[str, Any]:
    raw = args_json if args_json is not None else sys.stdin.read().strip()
    if not raw:
        raise ValueError("launch payload JSON is required")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("launch payload must be a JSON object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a Pi RPC worker from a search_start_agent_session launch payload."
    )
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--launch-json", help="Launch payload JSON. Defaults to stdin.")
    run_parser.add_argument("--pi-binary", default="pi")
    run_parser.add_argument("--extension", help="Path to search-runtime.ts")
    run_parser.add_argument("--model")
    run_parser.add_argument("--provider")
    run_parser.add_argument("--model-id")
    run_parser.add_argument("--thinking")
    run_parser.add_argument("--pretty", action="store_true")

    parsed = parser.parse_args(argv)
    if parsed.command not in {None, "run"}:
        parser.error("only the synchronous run command is supported")
    try:
        launch = _read_launch(getattr(parsed, "launch_json", None))
        handle = run_pi_rpc_worker(
            launch,
            pi_binary=getattr(parsed, "pi_binary", "pi"),
            extension_path=getattr(parsed, "extension", None),
            model_pattern=getattr(parsed, "model", None),
            provider=getattr(parsed, "provider", None),
            model_id=getattr(parsed, "model_id", None),
            thinking_level=getattr(parsed, "thinking", None),
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(
        json.dumps(
            handle,
            ensure_ascii=False,
            indent=2 if getattr(parsed, "pretty", False) else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
