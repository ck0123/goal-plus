from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
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


def run_pi_rpc_worker(
    launch: dict[str, Any],
    *,
    pi_binary: str = "pi",
    extension_path: Path | str | None = None,
    thinking_level: str | None = None,
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
    }
    cmd = [
        pi_binary,
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
    deadline = time.monotonic() + timeout_seconds

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
            if (
                not data.get("isStreaming", False)
                and not data.get("isCompacting", False)
                and int(data.get("pendingMessageCount") or 0) == 0
            ):
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
        _kill_process_group(proc)

    return {
        "host": "pi-rpc",
        "external_id": session_id,
        "metadata": {
            "session_dir": str(session_dir),
            "event_log": str(event_log),
            "text_log": str(text_log),
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
