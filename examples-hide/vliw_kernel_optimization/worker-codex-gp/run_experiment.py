#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any

from goal_plus.space_agent import (
    CodexSpaceReviewer,
    InterventionPlanProposal,
    SearchSpaceConfig,
    SpacePlanRecord,
    receive_review_packet,
    send_review_packet,
    space_review_socket_address,
)


WORKSPACE = Path(__file__).resolve().parent
VLIW_ROOT = WORKSPACE.parent
REPOSITORY_ROOT = WORKSPACE.parents[2]
DEFAULT_CONFIG = WORKSPACE / "experiment.json"
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "output" / "vliw-space-agent-current"
RUNTIME_ROOT = REPOSITORY_ROOT / ".gp"
STARTER_SOLUTION = VLIW_ROOT / "snapshots" / "starter_solution.py"
FRAMEWORK_SOURCE_PATHS = (
    ".codex/agents/search_candidate_agent.toml",
    ".codex/hooks.json",
    ".codex/skills/goal-plus/SKILL.md",
    ".codex/skills/search/SKILL.md",
    "src/goal_plus/agent_hosts.py",
    "src/goal_plus/goal_plus_stop_hook.py",
    "src/goal_plus/models.py",
    "src/goal_plus/runtime.py",
    "src/goal_plus/server.py",
    "src/goal_plus/space_agent.py",
    "src/goal_plus/tools.py",
    "examples-hide/vliw_kernel_optimization/prompts/codex-gp-space-3x1h.txt",
    "examples-hide/vliw_kernel_optimization/worker-codex-gp/experiment.json",
    "examples-hide/vliw_kernel_optimization/worker-codex-gp/run_experiment.py",
    "examples-hide/vliw_kernel_optimization/worker-codex-gp/space-schema.json",
    "examples-hide/vliw_kernel_optimization/worker-codex-gp/.goal-plus-verifiers/vliw_score.py",
)


def utc_text() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def checked(
    command: list[str],
    *,
    cwd: Path,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )


def resolve_config_path(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def load_config(path: Path) -> dict[str, Any]:
    config_path = path.resolve()
    config = read_json(config_path)
    required = {
        "config_version",
        "experiment_kind",
        "mode",
        "candidate_count",
        "max_parallel",
        "model",
        "reasoning_effort",
        "reviewer_model",
        "reviewer_reasoning_effort",
        "reviewer_profile",
        "reviewer_transport",
        "min_runtime_seconds",
        "max_runtime_seconds",
        "reviewer_timeout_seconds",
        "schema_consolidation_interval",
        "prompt",
        "schema",
        "verifier",
    }
    missing = sorted(required.difference(config))
    if missing:
        raise ValueError("experiment config is missing: " + ", ".join(missing))
    if config["config_version"] != 1:
        raise ValueError("unsupported experiment config_version")
    if config["mode"] != "enforce":
        raise ValueError("this runner supports only SpaceAgent mode=enforce")
    if config["candidate_count"] != 3 or config["max_parallel"] != 3:
        raise ValueError("the current VLIW experiment requires exactly 3 parallel lanes")
    if config["reasoning_effort"] not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("invalid reasoning_effort")
    if config["reviewer_reasoning_effort"] not in {
        "low",
        "medium",
        "high",
        "xhigh",
    }:
        raise ValueError("invalid reviewer_reasoning_effort")
    if config["reviewer_profile"] not in {
        "default_codex_home",
        "inherited_codex_home",
    }:
        raise ValueError("invalid reviewer_profile")
    if config["reviewer_transport"] not in {"output_schema", "plain_json"}:
        raise ValueError("invalid reviewer_transport")
    minimum = int(config["min_runtime_seconds"])
    maximum = int(config["max_runtime_seconds"])
    if minimum <= 0 or maximum <= minimum + 45:
        raise ValueError("max_runtime_seconds must leave over 45 seconds for closeout")
    reviewer_timeout = int(config["reviewer_timeout_seconds"])
    if not 1 <= reviewer_timeout <= 600:
        raise ValueError("reviewer_timeout_seconds must be between 1 and 600")
    interval = int(config["schema_consolidation_interval"])
    if not 2 <= interval <= 100:
        raise ValueError("schema_consolidation_interval must be between 2 and 100")

    config["config_path"] = str(config_path)
    for field in ("prompt", "schema", "verifier"):
        resolved = resolve_config_path(config_path, str(config[field]))
        if not resolved.is_file():
            raise FileNotFoundError(f"configured {field} does not exist: {resolved}")
        config[f"{field}_path"] = str(resolved)
    if Path(str(config["schema_path"])).parent != WORKSPACE:
        raise ValueError("space schema must live directly in worker-codex-gp")
    if not Path(str(config["verifier_path"])).is_relative_to(WORKSPACE):
        raise ValueError("verifier must live inside worker-codex-gp")
    return config


def framework_source_state() -> dict[str, Any]:
    commit = checked(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture=True,
    ).stdout.strip()
    status = checked(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        cwd=REPOSITORY_ROOT,
        capture=True,
    ).stdout
    tracked_diff = checked(
        ["git", "diff", "--binary", "HEAD", "--"],
        cwd=REPOSITORY_ROOT,
        capture=True,
    ).stdout
    file_hashes: dict[str, str] = {}
    for relative in FRAMEWORK_SOURCE_PATHS:
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"framework source file not found: {path}")
        file_hashes[relative] = sha256_file(path)
    fingerprint = {
        "commit": commit,
        "tracked_diff_sha256": hashlib.sha256(
            tracked_diff.encode("utf-8")
        ).hexdigest(),
        "files": file_hashes,
    }
    return {
        "source_commit": commit,
        "source_dirty": bool(status.strip()),
        "source_status": status.splitlines(),
        "source_status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "source_tracked_diff_sha256": fingerprint["tracked_diff_sha256"],
        "framework_files_sha256": file_hashes,
        "framework_source_sha256": hashlib.sha256(
            json.dumps(
                fingerprint,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest(),
    }


def ensure_workspace_baseline() -> str:
    if sha256_file(WORKSPACE / "solution.py") != sha256_file(STARTER_SOLUTION):
        raise RuntimeError(
            "worker-codex-gp/solution.py is not the frozen starter; reset it before a run"
        )
    git_dir = WORKSPACE / ".git"
    if not git_dir.exists():
        checked(["git", "init", "-q"], cwd=WORKSPACE)
        checked(
            ["git", "config", "user.email", "vliw-space-agent@localhost"],
            cwd=WORKSPACE,
        )
        checked(
            ["git", "config", "user.name", "VLIW SpaceAgent Experiment"],
            cwd=WORKSPACE,
        )
        checked(["git", "add", "-A"], cwd=WORKSPACE)
        checked(
            ["git", "commit", "-q", "-m", "frozen VLIW experiment baseline"],
            cwd=WORKSPACE,
        )
    top = checked(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=WORKSPACE,
        capture=True,
    ).stdout.strip()
    if Path(top).resolve() != WORKSPACE:
        raise RuntimeError("worker-codex-gp must use its own nested Git repository")
    status = checked(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        cwd=WORKSPACE,
        capture=True,
    ).stdout
    if status.strip():
        raise RuntimeError("worker-codex-gp nested baseline is dirty:\n" + status)
    return checked(
        ["git", "rev-parse", "HEAD"],
        cwd=WORKSPACE,
        capture=True,
    ).stdout.strip()


def review_socket_name(experiment_id: str) -> str:
    material = f"{WORKSPACE}:{experiment_id}"
    suffix = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"@gp-space-{suffix}"


def prepare_project_codex(socket_name: str) -> None:
    destination = WORKSPACE / ".codex"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        REPOSITORY_ROOT / ".codex",
        destination,
        ignore=shutil.ignore_patterns("config.toml", "__pycache__"),
    )
    (destination / "config.toml").write_text(
        "[mcp_servers.goal-plus]\n"
        'command = "goal-plus"\n'
        f'args = ["--root", "{RUNTIME_ROOT.as_posix()}"]\n'
        "startup_timeout_sec = 10\n"
        "tool_timeout_sec = 900\n"
        "enabled = true\n\n"
        "[mcp_servers.goal-plus.env]\n"
        f'GOAL_PLUS_SPACE_REVIEW_SOCKET = "{socket_name}"\n',
        encoding="utf-8",
    )


def prepare_codex_home(
    run_dir: Path,
    name: str,
    *,
    source: Path | None = None,
) -> Path:
    source = (
        source
        or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    ).resolve()
    destination = run_dir / name
    destination.mkdir()
    for filename in (
        "auth.json",
        "config.toml",
        "installation_id",
        "version.json",
        ".personality_migration",
    ):
        source_path = source / filename
        if source_path.is_file() and (
            filename != "auth.json" or source_path.stat().st_size > 0
        ):
            shutil.copy2(source_path, destination / filename)
    config_path = destination / "config.toml"
    if not config_path.is_file():
        raise RuntimeError(f"Codex config not found: {source / 'config.toml'}")
    config_text = config_path.read_text(encoding="utf-8")
    config_text = re.sub(
        r"\n\[plugins\.[^\n]+\]\n.*?(?=\n\[|\Z)",
        "\n",
        config_text,
        flags=re.DOTALL,
    )
    config_path.write_text(config_text, encoding="utf-8")
    for dirname in ("sessions", "log", "tmp", "shell_snapshots"):
        (destination / dirname).mkdir(exist_ok=True)
    return destination


class ConcurrentSpaceReviewerServer:
    def __init__(
        self,
        address: str,
        reviewer: CodexSpaceReviewer,
        *,
        max_concurrent_reviews: int,
    ) -> None:
        self.address = address
        self.reviewer = reviewer
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.settimeout(0.5)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.review_slots = threading.BoundedSemaphore(max_concurrent_reviews)
        self.lock = threading.Lock()
        self.handlers: list[threading.Thread] = []
        self.fatal_error: str | None = None
        self.requests = 0

    def start(self) -> None:
        self.listener.bind(space_review_socket_address(self.address))
        self.listener.listen(16)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.listener.close()
        self.thread.join(timeout=5)
        for handler in list(self.handlers):
            handler.join(timeout=5)

    def _serve(self) -> None:
        while not self.stop_event.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError as exc:
                if not self.stop_event.is_set():
                    self.fatal_error = f"review socket accept failed: {exc}"
                return
            handler = threading.Thread(
                target=self._handle,
                args=(connection,),
                daemon=True,
            )
            with self.lock:
                self.handlers.append(handler)
            handler.start()

    def _handle(self, connection: socket.socket) -> None:
        with connection, self.review_slots:
            with self.lock:
                self.requests += 1
            try:
                request = receive_review_packet(connection)
                config = SearchSpaceConfig.model_validate(request["config"])
                operation = str(request.get("operation") or "review")
                if operation == "consolidate":
                    execution = self.reviewer.consolidate(config)
                elif operation == "review":
                    proposal = InterventionPlanProposal.model_validate(
                        request["proposal"]
                    )
                    covered = [
                        SpacePlanRecord.model_validate(plan)
                        for plan in request.get("completed_plans") or []
                    ]
                    execution = self.reviewer.review(config, proposal, covered)
                else:
                    raise ValueError(f"unknown SpaceAgent operation: {operation}")
                response = {
                    "result": execution.result.model_dump(mode="json"),
                    "latency_ms": execution.latency_ms,
                    "usage": execution.usage,
                    "error": None,
                }
            except Exception as exc:
                response = {"error": f"{type(exc).__name__}: {exc}"}
            try:
                send_review_packet(connection, response)
            except (OSError, RuntimeError):
                return


def render_prompt(config: dict[str, Any], experiment_id: str) -> str:
    prompt = Path(str(config["prompt_path"])).read_text(encoding="utf-8")
    replacements = {
        "__EXPERIMENT_ID__": experiment_id,
        "__MIN_RUNTIME_SECONDS__": str(config["min_runtime_seconds"]),
        "__MAX_RUNTIME_SECONDS__": str(config["max_runtime_seconds"]),
        "__MODEL__": str(config["model"]),
        "__REASONING_EFFORT__": str(config["reasoning_effort"]),
        "__WORKSPACE__": WORKSPACE.as_posix(),
    }
    for marker, value in replacements.items():
        prompt = prompt.replace(marker, value)
    unresolved = sorted(set(re.findall(r"__[A-Z_]+__", prompt)))
    if unresolved:
        raise RuntimeError("unresolved prompt markers: " + ", ".join(unresolved))
    return prompt


def hidden_paths() -> list[Path]:
    paths = [
        VLIW_ROOT / "judge",
        VLIW_ROOT / "worker",
        VLIW_ROOT / "worker-claude",
        VLIW_ROOT / "snapshots",
        VLIW_ROOT / "prompts",
        REPOSITORY_ROOT / "output",
    ]
    return [path for path in paths if path.exists()]


def isolated_codex_command(
    *,
    codex: str,
    run_dir: Path,
    output_path: Path,
    config: dict[str, Any],
) -> list[str]:
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError(
            "bubblewrap is required because stateful MCP calls need Codex bypass mode"
        )
    command = [
        bwrap,
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    for path in hidden_paths():
        command.extend(["--tmpfs", str(path)])
    command.extend(
        [
            "--bind",
            str(WORKSPACE),
            str(WORKSPACE),
            "--bind",
            str(run_dir),
            str(run_dir),
            "--bind",
            str(RUNTIME_ROOT),
            str(RUNTIME_ROOT),
            "--chdir",
            str(WORKSPACE),
            codex,
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "-C",
            str(WORKSPACE),
            "-m",
            str(config["model"]),
            "-c",
            f'model_reasoning_effort="{config["reasoning_effort"]}"',
            "-o",
            str(output_path),
            "-",
        ]
    )
    return command


def event_label(line: str) -> str | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    if event_type in {
        "thread.started",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "error",
    }:
        return str(event_type)
    return None


def reviewer_fail_open_plans(experiment_id: str) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for config_path in (RUNTIME_ROOT / "runs").glob(
        "run_*/search-space/config.json"
    ):
        try:
            space_config = read_json(config_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if space_config.get("experiment_id") != experiment_id:
            continue
        for plan_path in sorted((config_path.parent / "plans").glob("ip-*.json")):
            try:
                plan = read_json(plan_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if plan.get("admission_source") == "reviewer_fail_open":
                failures.append(
                    {
                        "run_id": str(space_config.get("run_id") or ""),
                        "plan_id": str(plan.get("plan_id") or ""),
                        "candidate_id": str(plan.get("candidate_id") or ""),
                        "reviewer_error": str(plan.get("reviewer_error") or ""),
                    }
                )
    return failures


def reviewer_preflight(
    reviewer: CodexSpaceReviewer,
    config: dict[str, Any],
    experiment_id: str,
) -> dict[str, Any]:
    schema = read_json(Path(str(config["schema_path"])))
    base_config = SearchSpaceConfig(
        experiment_id=f"{experiment_id}-reviewer-preflight",
        run_id="run_reviewer_preflight",
        mode="enforce",
        schema_path="space-schema.json",
        schema_sha256=hashlib.sha256(
            json.dumps(
                schema,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest(),
        space_schema=schema,
        reviewer_model=str(config["reviewer_model"]),
        reviewer_reasoning_effort=str(config["reviewer_reasoning_effort"]),
        reviewer_timeout_seconds=int(config["reviewer_timeout_seconds"]),
        schema_consolidation_interval=int(config["schema_consolidation_interval"]),
        created_at=utc_text(),
    )
    execution = reviewer.review(
        base_config,
        InterventionPlanProposal(
            intervention=(
                "Classify a transport preflight with no material source mutation."
            ),
            scope="Reviewer transport only; no candidate workspace or evaluator run.",
            expected_new_information=(
                "Whether the configured reviewer returns a schema-valid decision."
            ),
        ),
        [],
    )
    if execution.result.decision != "accept":
        raise RuntimeError("reviewer preflight without coverage must be accepted")
    schema_with_state = json.loads(json.dumps(schema))
    schema_with_state["_runtime_search_state"] = {
        "state_version": 1,
        "evidence_revision": 1,
        "schema_snapshot_version": 1,
        "built_through_event_id": None,
        "coverage": [],
        "tail_events": [
            {
                "event_id": "se-000001",
                "event_index": 1,
                "candidate_id": "c001",
                "plan_id": "ip-0001",
                "proposal": {
                    "intervention": "Change one synthetic scheduler parameter.",
                    "scope": "Synthetic preflight context only.",
                    "expected_new_information": (
                        "Whether the Schema reviewer can consolidate one verified point."
                    ),
                },
                "realized_evidence": {
                    "artifact_delta_sha256": "preflight-delta",
                    "delta_files": ["solution.py"],
                    "delta_file_count": 1,
                    "changed_symbols": ["KernelBuilder"],
                    "changed_symbol_count": 1,
                    "diff_stat": "1 file changed",
                    "diff_excerpt": "- old_parameter\n+ new_parameter",
                    "diff_excerpt_truncated": False,
                    "metric_name": "cycles",
                    "metric_direction": "minimize",
                    "score_before": 100.0,
                    "score_after": 99.0,
                    "score_delta": -1.0,
                    "outcome": "improved",
                    "validity_passed": True,
                    "process_passed": True,
                    "infrastructure_failure": False,
                    "failure_class": None,
                },
                "coverage_eligible": True,
                "created_at": utc_text(),
            }
        ],
        "schema_refresh_due": True,
        "target_event_id": "se-000001",
    }
    schema_execution = reviewer.consolidate(
        base_config.model_copy(update={"space_schema": schema_with_state})
    )
    consolidated = schema_execution.result
    covered_event_ids = {
        event_id
        for entry in consolidated.coverage
        for event_id in entry.evidence_event_ids
    }
    if "_runtime_search_state" in consolidated.space_schema:
        raise RuntimeError("schema reviewer preflight leaked private runtime state")
    if "se-000001" not in consolidated.revision_evidence_event_ids:
        raise RuntimeError("schema reviewer preflight omitted its Evidence watermark")
    if "se-000001" not in covered_event_ids:
        raise RuntimeError("schema reviewer preflight omitted eligible coverage")
    return {
        "decision": execution.result.decision,
        "latency_ms": execution.latency_ms,
        "usage": execution.usage,
        "schema_consolidation": {
            "target_event_id": "se-000001",
            "latency_ms": schema_execution.latency_ms,
            "usage": schema_execution.usage,
        },
    }


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:  # pragma: no cover - Windows fallback
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows fallback
            process.kill()
        process.wait()


def run_codex(
    *,
    config: dict[str, Any],
    prompt_path: Path,
    run_dir: Path,
    manifest: dict[str, Any],
) -> int:
    codex = shutil.which("codex")
    if codex is None:
        raise RuntimeError("codex executable not found")
    codex_home = prepare_codex_home(run_dir, "codex-home")
    reviewer_source = (
        Path.home() / ".codex"
        if config["reviewer_profile"] == "default_codex_home"
        else None
    )
    reviewer_home = prepare_codex_home(
        run_dir,
        "reviewer-codex-home",
        source=reviewer_source,
    )
    output_path = run_dir / "codex-final.txt"
    command = isolated_codex_command(
        codex=codex,
        run_dir=run_dir,
        output_path=output_path,
        config=config,
    )
    reviewer = CodexSpaceReviewer(
        hidden_paths=[VLIW_ROOT, REPOSITORY_ROOT / "output"],
        codex_home=reviewer_home,
        scratch_root=run_dir / "reviewer-tmp",
        use_output_schema=config["reviewer_transport"] == "output_schema",
    )
    manifest["reviewer_preflight"] = reviewer_preflight(
        reviewer,
        config,
        str(manifest["experiment_id"]),
    )
    atomic_json(run_dir / "manifest.json", manifest)
    review_server = ConcurrentSpaceReviewerServer(
        str(manifest["review_socket_address"]),
        reviewer,
        max_concurrent_reviews=int(config["max_parallel"]),
    )
    review_server.start()
    started_wall = time.time()
    started_mono = time.monotonic()
    outer_timeout = int(config["max_runtime_seconds"]) + 1800
    manifest.update(
        {
            "status": "running",
            "started_at": utc_text(),
            "outer_timeout_seconds": outer_timeout,
            "codex_command": command[:-1] + ["<prompt on stdin>"],
            "isolation": {
                "tool": "bubblewrap",
                "root": "read-only",
                "writable": [
                    str(WORKSPACE),
                    str(run_dir),
                    str(RUNTIME_ROOT),
                    "/tmp",
                ],
                "hidden": [str(path) for path in hidden_paths()],
            },
        }
    )
    atomic_json(run_dir / "manifest.json", manifest)
    environment = {
        **os.environ,
        "CODEX_HOME": str(codex_home),
        "GOAL_PLUS_PROJECT_ROOT": str(WORKSPACE),
        "GOAL_PLUS_SEARCH_ROOT": str(RUNTIME_ROOT),
    }
    process = subprocess.Popen(
        command,
        cwd=WORKSPACE,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert process.stdin is not None
    process.stdin.write(prompt_path.read_text(encoding="utf-8"))
    process.stdin.close()

    stdout_path = run_dir / "codex.jsonl"
    stderr_path = run_dir / "codex.stderr.log"

    def drain_stdout() -> None:
        assert process.stdout is not None
        with stdout_path.open("w", encoding="utf-8") as output:
            for line in process.stdout:
                output.write(line)
                output.flush()
                label = event_label(line)
                if label is not None:
                    print(f"[{utc_text()}] codex event: {label}", flush=True)

    def drain_stderr() -> None:
        assert process.stderr is not None
        with stderr_path.open("w", encoding="utf-8") as output:
            for line in process.stderr:
                output.write(line)
                output.flush()
                print(f"[codex stderr] {line.rstrip()}", file=sys.stderr, flush=True)

    drain_threads = [
        threading.Thread(target=drain_stdout, daemon=True),
        threading.Thread(target=drain_stderr, daemon=True),
    ]
    for thread in drain_threads:
        thread.start()

    next_heartbeat = time.monotonic() + 60
    timed_out = False
    reviewer_fail_open: list[dict[str, str]] = []
    try:
        while process.poll() is None:
            time.sleep(2)
            elapsed = time.monotonic() - started_mono
            reviewer_fail_open = reviewer_fail_open_plans(
                str(manifest["experiment_id"])
            )
            if reviewer_fail_open:
                terminate_process_group(process)
                break
            if elapsed >= outer_timeout:
                timed_out = True
                terminate_process_group(process)
                break
            if time.monotonic() >= next_heartbeat:
                print(
                    f"[{utc_text()}] experiment running; "
                    f"elapsed_seconds={int(time.time() - started_wall)}; "
                    f"space_reviews={review_server.requests}",
                    flush=True,
                )
                next_heartbeat = time.monotonic() + 60
    except KeyboardInterrupt:
        terminate_process_group(process)
        raise
    finally:
        for thread in drain_threads:
            thread.join(timeout=10)
        review_server.stop()
    manifest["review_socket_requests"] = review_server.requests
    manifest["review_socket_error"] = review_server.fatal_error
    manifest["reviewer_fail_open"] = reviewer_fail_open
    manifest["outer_timed_out"] = timed_out
    atomic_json(run_dir / "manifest.json", manifest)
    if reviewer_fail_open:
        return 125
    return 124 if timed_out else int(process.returncode or 0)


def latest_run(runtime_root: Path) -> tuple[str | None, dict[str, Any] | None]:
    run_paths = sorted(
        (runtime_root / "runs").glob("run_*/run.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not run_paths:
        return None, None
    payload = read_json(run_paths[0])
    run_id = payload.get("run_id")
    return (str(run_id), payload) if run_id else (None, payload)


def parse_evaluation(text: str) -> dict[str, float | None]:
    result: dict[str, float | None] = {"public_cycles": None, "hidden_cycles": None}
    sections = re.split(r"^=====\s+", text, flags=re.MULTILINE)
    for section in sections:
        label = None
        if section.startswith("public "):
            label = "public_cycles"
        elif section.startswith("hidden "):
            label = "hidden_cycles"
        if label is None or re.search(
            r"^all_correct=True$", section, re.MULTILINE
        ) is None:
            continue
        match = re.search(
            r"^score_cycles=(\d+(?:\.\d+)?)$",
            section,
            re.MULTILINE,
        )
        if match is None:
            match = re.search(
                r"^Score:\s*(\d+(?:\.\d+)?)$",
                section,
                re.MULTILINE,
            )
        if match is not None:
            result[label] = float(match.group(1))
    return result


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def space_summary(runtime_root: Path, run_id: str) -> dict[str, Any]:
    space_dir = runtime_root / "runs" / run_id / "search-space"
    if not (space_dir / "config.json").is_file():
        return {"enabled": False, "error": "search-space config not found"}
    raw_plans = [
        read_json(path) for path in sorted((space_dir / "plans").glob("ip-*.json"))
    ]
    plan_by_id = {str(plan.get("plan_id")): plan for plan in raw_plans}
    status_counts = Counter(str(plan.get("status")) for plan in raw_plans)
    admission_counts = Counter(
        str(plan.get("admission_source"))
        for plan in raw_plans
        if plan.get("admission_source")
    )
    decision_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    per_candidate: dict[str, Counter[str]] = defaultdict(Counter)
    duplicate_rejections: list[dict[str, Any]] = []
    first_created = min(
        (
            timestamp
            for plan in raw_plans
            if (timestamp := parse_timestamp(str(plan.get("created_at") or "")))
            is not None
        ),
        default=None,
    )
    time_buckets: dict[str, Counter[str]] = defaultdict(Counter)

    for plan in raw_plans:
        candidate_id = str(plan.get("candidate_id") or "unknown")
        per_candidate[candidate_id]["proposed"] += 1
        review = plan.get("review") if isinstance(plan.get("review"), dict) else None
        decision = str(review.get("decision")) if review else "unreviewed"
        decision_counts[decision] += 1
        per_candidate[candidate_id][decision] += 1
        if plan.get("status") == "completed":
            per_candidate[candidate_id]["completed"] += 1
        realized = plan.get("realized_evidence")
        if isinstance(realized, dict) and realized.get("outcome"):
            outcome = str(realized["outcome"])
            outcome_counts[outcome] += 1
            per_candidate[candidate_id][f"outcome_{outcome}"] += 1

        created = parse_timestamp(str(plan.get("created_at") or ""))
        if first_created is None or created is None:
            bucket = "unknown"
        else:
            minute = (created - first_created) / 60.0
            bucket = "00-20" if minute < 20 else ("20-40" if minute < 40 else "40+")
        time_buckets[bucket]["proposed"] += 1
        if decision == "reject":
            time_buckets[bucket]["rejected"] += 1

        if not review:
            continue
        if review.get("reason_code"):
            reason_counts[str(review["reason_code"])] += 1
        if decision != "reject":
            continue
        duplicate_ids = [str(value) for value in review.get("duplicate_of") or []]
        duplicate_lanes = sorted(
            {
                str(plan_by_id[duplicate_id].get("candidate_id"))
                for duplicate_id in duplicate_ids
                if duplicate_id in plan_by_id
            }
        )
        same_lane = candidate_id in duplicate_lanes
        cross_lane = any(lane != candidate_id for lane in duplicate_lanes)
        collision_scope = (
            "mixed"
            if same_lane and cross_lane
            else ("cross_lane" if cross_lane else "same_lane")
        )
        duplicate_rejections.append(
            {
                "plan_id": plan.get("plan_id"),
                "candidate_id": candidate_id,
                "duplicate_of": duplicate_ids,
                "duplicate_candidate_ids": duplicate_lanes,
                "collision_scope": collision_scope,
                "reason_code": review.get("reason_code"),
                "region_key": review.get("region_key"),
                "point_key": review.get("point_key"),
                "confidence": review.get("confidence"),
                "plan_card": {
                    key: (plan.get("proposal") or {}).get(key)
                    for key in (
                        "intervention",
                        "scope",
                        "expected_new_information",
                    )
                },
            }
        )

    reviewed = decision_counts["accept"] + decision_counts["reject"]
    cross_lane_rejections = sum(
        item["collision_scope"] in {"cross_lane", "mixed"}
        for item in duplicate_rejections
    )
    same_lane_rejections = sum(
        item["collision_scope"] in {"same_lane", "mixed"}
        for item in duplicate_rejections
    )
    bucket_summary = {
        bucket: {
            **dict(counts),
            "rejection_rate": rate(counts["rejected"], counts["proposed"]),
        }
        for bucket, counts in sorted(time_buckets.items())
    }
    schema_paths = sorted((space_dir / "schemas").glob("schema-*.json"))
    event_paths = sorted((space_dir / "events").glob("se-*.json"))
    state = read_json(space_dir / "state.json") if (space_dir / "state.json").is_file() else {}
    return {
        "enabled": True,
        "plan_count": len(raw_plans),
        "status_counts": dict(status_counts),
        "admission_source_counts": dict(admission_counts),
        "decision_counts": dict(decision_counts),
        "reason_counts": dict(reason_counts),
        "outcome_counts": dict(outcome_counts),
        "reviewed_plan_count": reviewed,
        "duplicate_rejection_count": decision_counts["reject"],
        "duplicate_rejection_rate": rate(decision_counts["reject"], reviewed),
        "same_lane_rejection_count": same_lane_rejections,
        "cross_lane_rejection_count": cross_lane_rejections,
        "reviewer_fail_open_count": admission_counts["reviewer_fail_open"],
        "review_retry_count": sum(
            max(0, int(plan.get("review_attempts") or 0) - 1)
            for plan in raw_plans
        ),
        "per_candidate": {
            candidate_id: dict(counts)
            for candidate_id, counts in sorted(per_candidate.items())
        },
        "by_elapsed_minutes": bucket_summary,
        "evidence_event_count": len(event_paths),
        "schema_snapshot_count": len(schema_paths),
        "schema_consolidation": {
            key: state.get(key)
            for key in (
                "schema_consolidation_attempts",
                "schema_consolidation_successes",
                "schema_consolidation_failures",
                "schema_reviewer_latency_ms_total",
                "schema_reviewer_usage",
                "last_schema_consolidation_error",
            )
        },
        "state_revision": {
            key: state.get(key)
            for key in (
                "state_version",
                "admission_revision",
                "evidence_revision",
                "schema_revision",
            )
        },
        "duplicate_rejections": duplicate_rejections,
    }


def post_search_evaluate(runtime_root: Path, run_dir: Path) -> dict[str, Any]:
    run_id, run_payload = latest_run(runtime_root)
    result: dict[str, Any] = {
        "run_id": run_id,
        "run_state": run_payload.get("state") if run_payload else None,
        "selected_candidate_id": (
            run_payload.get("selected_candidate_id") if run_payload else None
        ),
        "selected_iteration": (
            run_payload.get("selected_iteration") if run_payload else None
        ),
        "selected_git_head": (
            run_payload.get("selected_git_head") if run_payload else None
        ),
        "runtime_best_score": run_payload.get("best_score") if run_payload else None,
    }
    if run_id is None or run_payload is None:
        result["evaluation_error"] = "no Search run found"
        return result
    result["space"] = space_summary(runtime_root, run_id)
    candidate_id = run_payload.get("selected_candidate_id")
    selected_head = run_payload.get("selected_git_head")
    if (
        run_payload.get("state") != "promoted"
        or not isinstance(candidate_id, str)
        or not selected_head
    ):
        result["evaluation_error"] = (
            "hidden evaluation requires a promoted verifier-backed candidate"
        )
        result["evaluation_passed"] = False
        return result
    candidate_workspace = (
        runtime_root
        / "runs"
        / run_id
        / "workspace"
        / candidate_id
    )
    workspace_solution = candidate_workspace / "solution.py"
    result["candidate_workspace_solution"] = str(workspace_solution)
    if not workspace_solution.is_file():
        result["evaluation_error"] = (
            f"candidate solution not found: {workspace_solution}"
        )
        result["evaluation_passed"] = False
        return result
    workspace_head = checked(
        ["git", "rev-parse", "HEAD"],
        cwd=candidate_workspace,
        capture=True,
    ).stdout.strip()
    selected_source = checked(
        ["git", "show", f"{selected_head}:solution.py"],
        cwd=candidate_workspace,
        capture=True,
    ).stdout
    candidate = run_dir / "selected-solution.py"
    candidate.write_text(selected_source, encoding="utf-8")
    candidate.chmod(0o444)
    result["candidate_solution"] = str(candidate)
    result["workspace_git_head"] = workspace_head
    result["evaluated_git_head"] = selected_head
    result["workspace_matches_selected_solution"] = (
        sha256_file(workspace_solution) == sha256_file(candidate)
    )
    completed = subprocess.run(
        [sys.executable, "evaluate.py", str(candidate), "--cases", "both"],
        cwd=VLIW_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    evaluation_text = completed.stdout + completed.stderr
    (run_dir / "post-search-evaluation.txt").write_text(
        evaluation_text,
        encoding="utf-8",
    )
    result.update(parse_evaluation(evaluation_text))
    result["evaluation_returncode"] = completed.returncode
    result["candidate_solution_sha256"] = sha256_file(candidate)
    result["evaluation_passed"] = bool(
        completed.returncode == 0
        and result.get("public_cycles") is not None
        and result.get("hidden_cycles") is not None
    )
    if not result["evaluation_passed"]:
        result["evaluation_error"] = (
            f"post-search evaluation failed or was incomplete (exit {completed.returncode})"
        )
    return result


def prepare_run(
    *,
    config: dict[str, Any],
    experiment_id: str,
    output_root: Path,
) -> tuple[Path, Path, dict[str, Any]]:
    run_dir = output_root.resolve() / experiment_id
    if run_dir.exists():
        raise RuntimeError(f"experiment output already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    baseline_head = ensure_workspace_baseline()
    socket_name = review_socket_name(experiment_id)
    prepare_project_codex(socket_name)
    prompt_path = run_dir / "prompt.txt"
    prompt_path.write_text(render_prompt(config, experiment_id), encoding="utf-8")
    manifest: dict[str, Any] = {
        "manifest_version": 2,
        "experiment_id": experiment_id,
        "status": "prepared",
        "prepared_at": utc_text(),
        **framework_source_state(),
        "workspace": str(WORKSPACE),
        "runtime_root": str(RUNTIME_ROOT),
        "baseline_workspace_head": baseline_head,
        "config": {
            key: value
            for key, value in config.items()
            if not key.endswith("_path") and key != "config_path"
        },
        "config_sha256": sha256_file(Path(str(config["config_path"]))),
        "prompt_path": str(prompt_path),
        "prompt_sha256": sha256_file(prompt_path),
        "schema_sha256": sha256_file(Path(str(config["schema_path"]))),
        "verifier_sha256": sha256_file(Path(str(config["verifier_path"]))),
        "public_cases_sha256": sha256_file(
            WORKSPACE / "test_cases" / "public_cases.json"
        ),
        "starter_solution_sha256": sha256_file(WORKSPACE / "solution.py"),
        "review_socket_address": socket_name,
        "hidden_visibility": "post-search harness only",
        "environment": {
            "python": sys.version.split()[0],
            "codex_path": shutil.which("codex"),
            "goal_plus_path": shutil.which("goal-plus"),
            "bubblewrap_path": shutil.which("bwrap"),
        },
    }
    atomic_json(run_dir / "manifest.json", manifest)
    return run_dir, prompt_path, manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the current 3-lane VLIW SpaceAgent experiment in place."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--experiment-id")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--prepare-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    experiment_id = args.experiment_id or (
        "vliw-space-3x1h-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", experiment_id):
        raise SystemExit("experiment id must use letters, digits, dot, underscore, or dash")
    run_dir, prompt_path, manifest = prepare_run(
        config=config,
        experiment_id=experiment_id,
        output_root=args.output_root,
    )
    print(f"prepared current VLIW SpaceAgent run: {run_dir}", flush=True)
    if args.prepare_only:
        return 0

    returncode = run_codex(
        config=config,
        prompt_path=prompt_path,
        run_dir=run_dir,
        manifest=manifest,
    )
    result = post_search_evaluate(RUNTIME_ROOT, run_dir)
    result.update({"codex_returncode": returncode, "completed_at": utc_text()})
    experiment_passed = bool(
        returncode == 0
        and result.get("run_state") == "promoted"
        and result.get("evaluation_passed") is True
    )
    harness_returncode = returncode if returncode != 0 else (0 if experiment_passed else 1)
    result["harness_returncode"] = harness_returncode
    atomic_json(run_dir / "result.json", result)
    manifest.update(
        {
            "status": (
                "completed"
                if experiment_passed
                else ("codex_failed" if returncode != 0 else "evaluation_failed")
            ),
            "completed_at": result["completed_at"],
            "codex_returncode": returncode,
            "harness_returncode": harness_returncode,
            "run_id": result.get("run_id"),
            "run_state": result.get("run_state"),
            "public_cycles": result.get("public_cycles"),
            "hidden_cycles": result.get("hidden_cycles"),
        }
    )
    atomic_json(run_dir / "manifest.json", manifest)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return harness_returncode


if __name__ == "__main__":
    raise SystemExit(main())
