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

from goal_plus.paths import DEFAULT_RUNTIME_ROOT


class PiRpcError(RuntimeError):
    pass


def _bounded_error(value: Any, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return _bounded_error(value, limit=500)
    if isinstance(value, dict):
        return {
            str(key)[:100]: _bounded_json(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, list):
        return [_bounded_json(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value if len(value) <= 2000 else value[:2000] + "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded_error(value, limit=500)


def _git_snapshot(workspace: Path) -> dict[str, Any]:
    if not (workspace / ".git").exists():
        return {
            "git_head": None,
            "dirty": False,
            "git_status": [],
            "changed_files": [],
            "diff_stat": None,
        }

    def output(args: list[str]) -> str | None:
        try:
            completed = subprocess.run(
                args,
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        return completed.stdout.rstrip("\n")

    head_output = output(["git", "rev-parse", "--verify", "HEAD"])
    head = head_output.strip() if head_output else None
    status_text = output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"]
    )
    status = [line for line in (status_text or "").splitlines() if line.strip()]

    def ignored(path: str) -> bool:
        parts = Path(path).parts
        return (
            path.startswith(".tmp/")
            or "__pycache__" in parts
            or path.endswith((".pyc", ".pyo"))
        )

    changed_files: list[str] = []
    for line in status:
        path = line[3:] if len(line) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and not ignored(path):
            changed_files.append(path)
    diff_stat = output(["git", "diff", "--stat", "HEAD"]) if head else None
    return {
        "git_head": head,
        "dirty": bool(changed_files),
        "git_status": [
            line
            for line in status
            if not ignored(line[3:] if len(line) > 3 else "")
        ][:100],
        "changed_files": sorted(set(changed_files))[:100],
        "diff_stat": _bounded_error(diff_stat, limit=4000),
    }


def _verifier_snapshot(root: Path, run_id: str, candidate_id: str) -> dict[str, Any]:
    candidate_path = root / "runs" / run_id / "candidates" / candidate_id / "candidate.json"
    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {
            "count": 0,
            "best_iteration": None,
            "best_score": None,
            "best_git_head": None,
        }
    iterations = payload.get("iterations")
    records = iterations if isinstance(iterations, list) else []
    valid = [
        item
        for item in records
        if isinstance(item, dict)
        and item.get("process_passed") is True
        and not item.get("touched_denied_files")
        and not item.get("changed_outside_allowed")
        and isinstance(item.get("score"), (int, float))
    ]
    direction = "maximize"
    try:
        run_payload = json.loads(
            (root / "runs" / run_id / "run.json").read_text(encoding="utf-8")
        )
        spec_payload = json.loads(
            (
                root
                / "specs"
                / str(run_payload["frozen_spec_id"])
                / "frozen_spec.json"
            ).read_text(encoding="utf-8")
        )
        if spec_payload.get("spec", {}).get("metric_direction") == "minimize":
            direction = "minimize"
    except (FileNotFoundError, KeyError, OSError, json.JSONDecodeError, TypeError):
        pass
    chooser = min if direction == "minimize" else max
    best = chooser(valid, key=lambda item: float(item["score"]), default=None)
    return {
        "count": len(records),
        "best_iteration": best.get("iteration") if best else None,
        "best_score": best.get("score") if best else None,
        "best_git_head": best.get("git_head") if best else None,
    }


def _workspace_progress_handoff(
    workspace: Path,
    *,
    root: Path,
    run_id: str,
    candidate_id: str,
    timed_out: bool,
    runner_failed: bool,
    assistant_text: str | None,
) -> dict[str, Any]:
    model_handoff: dict[str, Any] | None = None
    handoff_path = workspace / ".tmp" / "handoff.json"
    try:
        parsed = json.loads(handoff_path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            model_handoff = _bounded_json(parsed)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass

    workspace_state = _git_snapshot(workspace)
    verifier_state = _verifier_snapshot(root, run_id, candidate_id)
    assistant_summary = _bounded_error(assistant_text, limit=2000)
    summary = (
        model_handoff.get("summary")
        if model_handoff and isinstance(model_handoff.get("summary"), str)
        else assistant_summary
    )
    if not summary:
        changed_files = workspace_state["changed_files"]
        summary = (
            "Workspace has unverified changes in: " + ", ".join(changed_files)
            if changed_files
            else "No verifier-backed progress recorded."
        )
    return {
        "status": "runner_failed" if runner_failed else "timed_out" if timed_out else "completed",
        "summary": summary,
        "model_handoff": model_handoff,
        "assistant_summary": assistant_summary,
        "workspace": workspace_state,
        "verifier": verifier_state,
    }


def _message_summary(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    content = message.get("content")
    content_types = (
        [str(item.get("type")) for item in content if isinstance(item, dict) and item.get("type")]
        if isinstance(content, list)
        else []
    )
    summary: dict[str, Any] = {
        "role": message.get("role"),
        "content_types": content_types,
    }
    if message.get("stopReason") is not None:
        summary["stop_reason"] = message["stopReason"]
    if isinstance(message.get("usage"), dict):
        summary["usage"] = message["usage"]
    if message.get("errorMessage"):
        summary["error"] = _bounded_error(message["errorMessage"])
    return summary


def _compact_pi_event(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("type") or "unknown")
    if event_type == "message_update":
        return None
    if event_type in {"message_start", "message_end"}:
        return {"type": event_type, **_message_summary(event.get("message"))}
    if event_type == "turn_end":
        tool_results = event.get("toolResults")
        return {
            "type": event_type,
            **_message_summary(event.get("message")),
            "tool_result_count": len(tool_results) if isinstance(tool_results, list) else 0,
        }
    if event_type == "agent_end":
        messages = event.get("messages")
        last_message = messages[-1] if isinstance(messages, list) and messages else None
        return {
            "type": event_type,
            "message_count": len(messages) if isinstance(messages, list) else 0,
            "will_retry": bool(event.get("willRetry")),
            "last_message": _message_summary(last_message),
        }
    if event_type.startswith("tool_execution_"):
        summary = {
            "type": event_type,
            "tool_call_id": event.get("toolCallId"),
            "tool_name": event.get("toolName"),
        }
        if event.get("isError") is not None:
            summary["is_error"] = bool(event.get("isError"))
        if event.get("isError"):
            summary["error"] = _bounded_error(event.get("result"))
        return summary
    if event_type == "response":
        summary = {
            "type": event_type,
            "id": event.get("id"),
            "command": event.get("command"),
            "success": bool(event.get("success")),
        }
        data = event.get("data")
        if isinstance(data, dict):
            summary["data_keys"] = sorted(str(key) for key in data)
            entries = data.get("entries")
            if isinstance(entries, list):
                summary["entry_count"] = len(entries)
            if event.get("command") == "get_state":
                for key in ("isStreaming", "isCompacting", "pendingMessageCount", "messageCount"):
                    if key in data:
                        summary[key] = data[key]
        if event.get("error"):
            summary["error"] = _bounded_error(event.get("error"))
        return summary
    if event_type in {"stderr", "raw_stdout"}:
        return {"type": event_type, "error": _bounded_error(event.get("text"))}
    if event_type.startswith("auto_retry_"):
        return {
            "type": event_type,
            **{
                key: event[key]
                for key in ("attempt", "maxAttempts", "delayMs", "success")
                if key in event
            },
            **({"error": _bounded_error(event.get("error"))} if event.get("error") else {}),
        }
    if event_type == "queue_update":
        return {
            "type": event_type,
            **{
                key: event[key]
                for key in ("steeringMessageCount", "followUpMessageCount", "pendingMessageCount")
                if key in event
            },
        }
    return {"type": event_type}


class _RpcClient:
    def __init__(
        self,
        *,
        proc: subprocess.Popen[str],
        event_log: Path,
        text_log: Path | None,
        raw_logging: bool = False,
    ) -> None:
        self.proc = proc
        self.event_log = event_log
        self.text_log = text_log
        self.raw_logging = raw_logging
        self._condition = threading.Condition()
        self._responses: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._auto_retry_until = 0.0
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)

    def start(self) -> None:
        self.event_log.parent.mkdir(parents=True, exist_ok=True)
        if self.text_log is not None:
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
        if not self.raw_logging:
            compact_event = _compact_pi_event(event)
            if compact_event is None:
                return
            event = compact_event
        with self.event_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _append_text(self, line: str) -> None:
        if self.text_log is None:
            return
        with self.text_log.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def auto_retry_pending(self) -> bool:
        with self._condition:
            return time.monotonic() < self._auto_retry_until


def default_extension_path() -> Path:
    source_root = Path(__file__).resolve().parents[2]
    return source_root / ".pi" / "extensions" / "goal-plus.ts"


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


def _collect_pi_metrics(
    rpc: Any,
    *,
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

    baseline_count = len(baseline_entries)
    delta_entries = final_entries[baseline_count:] if len(final_entries) >= baseline_count else []
    scope = "run_delta" if not baseline_error and final_entries else "session_total_fallback"
    metrics: dict[str, Any] = {
        "scope": scope,
        "session_id": session_id,
        "session_file": None,
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
    root = Path(
        str(launch.get("root") or os.environ.get("GOAL_PLUS_ROOT", DEFAULT_RUNTIME_ROOT))
    ).resolve()
    cwd = Path(str(launch["cwd"])).resolve()
    budget = dict(launch.get("budget_control") or {})
    max_runtime_seconds = budget.get("max_runtime_seconds")
    if max_runtime_seconds is None:
        raise ValueError("pi_rpc_worker launch requires budget_control.max_runtime_seconds")
    timeout_seconds = int(max_runtime_seconds)
    soft_closeout_seconds = int(
        budget.get("soft_closeout_seconds")
        or min(45, max(5, timeout_seconds // 5))
    )

    host_logs = root / "host-logs"
    event_log = host_logs / f"pi-rpc-{session_id}.jsonl"
    raw_logging = os.environ.get("GOAL_PLUS_PI_RAW_LOG") == "1"
    text_log = host_logs / f"pi-rpc-{session_id}.txt" if raw_logging else None
    extension = Path(extension_path) if extension_path else default_extension_path()
    if not extension.exists():
        raise FileNotFoundError(f"Pi extension not found: {extension}")

    host_logs.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GOAL_PLUS_ROOT": str(root),
        "GOAL_PLUS_PI_ROLE": "worker",
        "GOAL_PLUS_SOURCE_PATH": str(default_extension_path().parents[2]),
    }
    selected_model_pattern = (
        model_pattern
        or launch.get("model_pattern")
        or os.environ.get("GOAL_PLUS_PI_MODEL")
    )
    cmd = [pi_binary]
    if selected_model_pattern:
        cmd.extend(["--model", selected_model_pattern])
    cmd.extend(
        [
            "--mode",
            "rpc",
            "--approve",
            "--no-session",
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
    rpc = _RpcClient(
        proc=proc,
        event_log=event_log,
        text_log=text_log,
        raw_logging=raw_logging,
    )
    rpc.start()

    assistant_text: str | None = None
    timed_out = False
    soft_closeout_sent = False
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
            worker_active = (
                data.get("isStreaming", False)
                or data.get("isCompacting", False)
                or int(data.get("pendingMessageCount") or 0) > 0
            )
            remaining_after_state = deadline - time.monotonic()
            if (
                worker_active
                and not soft_closeout_sent
                and remaining_after_state <= soft_closeout_seconds
            ):
                soft_closeout_sent = True
                try:
                    rpc.command(
                        {
                            "type": "steer",
                            "message": (
                                "Worker deadline is approaching. Stop starting new analysis, edits, "
                                "or optimization iterations. If the workspace changed since the latest "
                                "recorded verifier, run one final search_run_verifier now; otherwise "
                                "return a concise summary immediately. Do not start another optimization "
                                "iteration."
                            ),
                        },
                        timeout=min(5, max(0.1, remaining_after_state)),
                    )
                except Exception:
                    pass
            if not worker_active:
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

    progress_handoff = _workspace_progress_handoff(
        cwd,
        root=root,
        run_id=str(launch.get("run_id") or ""),
        candidate_id=str(launch.get("candidate_id") or ""),
        timed_out=timed_out,
        runner_failed=False,
        assistant_text=assistant_text,
    )

    return {
        "host": "pi-rpc",
        "external_id": session_id,
        "metadata": {
            "event_log": str(event_log),
            "text_log": str(text_log) if text_log is not None else None,
            "raw_logging": raw_logging,
            "session_file": pi_metrics.get("session_file") if pi_metrics else None,
            "pi_metrics": pi_metrics,
            "assistant_text": assistant_text,
            "progress_handoff": progress_handoff,
            "timed_out": timed_out,
            "soft_closeout_seconds": soft_closeout_seconds,
            "soft_closeout_sent": soft_closeout_sent,
            "exit_code": proc.returncode,
            "continuation": "state_redispatch",
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
    run_parser.add_argument("--extension", help="Path to goal-plus.ts")
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
