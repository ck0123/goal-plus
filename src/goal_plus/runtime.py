from __future__ import annotations

from contextlib import contextmanager
import calendar
import hashlib
import importlib
import json
import math
import os
import random
import signal
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None  # type: ignore[assignment]

from goal_plus.agent_hosts import (
    UnsupportedHostCapability,
    get_agent_host_adapter,
    portable_strategy_mode,
)
from goal_plus.models import (
    AgentHostHandle,
    AgentSessionRecord,
    CandidateRecord,
    CandidateProposal,
    CandidateTask,
    CandidateWorkOrder,
    FrozenSpec,
    HistoryPolicy,
    ProposalContract,
    PromotionEvidence,
    RunRecord,
    RunState,
    RunSummary,
    IterationRecord,
    ScoreReport,
    SearchPlan,
    SearchSpec,
    StrategySpec,
    VerifierCommand,
    VerifierResult,
    VerifierRole,
    WorkerBudget,
)
from goal_plus.paths import DEFAULT_RUNTIME_ROOT, LEGACY_RUNTIME_ROOT
from goal_plus.workspaces import (
    IGNORED_NAMES,
    IGNORED_SUFFIXES,
    copy_source_tree,
    initialize_workspace_git_baseline,
    list_files,
    list_source_files,
    materialize_candidate_workspace,
)


CLAUDE_CODE_KNOWN_AGENT_TURN_BUDGETS = {
    "search-candidate-agent-flash": 4,
    "search-candidate-agent": 8,
    "search-candidate-agent-deep": 16,
}
CLAUDE_CODE_AGENT_TYPE_BY_TURN_BUDGET = {
    turns: agent_type
    for agent_type, turns in CLAUDE_CODE_KNOWN_AGENT_TURN_BUDGETS.items()
}
VERIFIER_PHASE_ENV = "GOAL_PLUS_VERIFIER_PHASE"
VERIFIER_DIAGNOSTICS_ENV = "GOAL_PLUS_VERIFIER_DIAGNOSTICS_DIR"
VERIFIER_RESOURCE_ENV = "GOAL_PLUS_VERIFIER_RESOURCE"
VERIFIER_RESOURCE_LOCK_DIR_ENV = "GOAL_PLUS_VERIFIER_RESOURCE_LOCK_DIR"
VERIFIER_OUTPUT_LIMIT_BYTES = 64 * 1024
VERIFIER_LOG_LIMIT_BYTES = VERIFIER_OUTPUT_LIMIT_BYTES * 2 + 8192
VERIFIER_TERM_GRACE_SECONDS = 0.5


class _BoundedOutput:
    def __init__(self, limit: int = VERIFIER_OUTPUT_LIMIT_BYTES) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        if len(chunk) >= self.limit:
            self.data[:] = chunk[-self.limit :]
            self.truncated = True
            return
        overflow = len(self.data) + len(chunk) - self.limit
        if overflow > 0:
            del self.data[:overflow]
            self.truncated = True
        self.data.extend(chunk)

    def text(self) -> str:
        value = self.data.decode("utf-8", errors="replace")
        if self.truncated:
            return "[... output truncated ...]\n" + value
        return value


def _bounded_log(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= VERIFIER_LOG_LIMIT_BYTES:
        return value
    marker = b"[... log truncated ...]\n"
    tail = encoded[-(VERIFIER_LOG_LIMIT_BYTES - len(marker)) :]
    return (marker + tail).decode("utf-8", errors="replace")


def _verifier_output_tail_detail(stdout: str, stderr: str) -> str:
    details = []
    stdout_tail = stdout.strip()[-2000:]
    stderr_tail = stderr.strip()[-2000:]
    if stdout_tail:
        details.append(f"Stdout tail: {stdout_tail}")
    if stderr_tail:
        details.append(f"Stderr tail: {stderr_tail}")
    return " " + " ".join(details) if details else ""


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def utc_timestamp_from_epoch(epoch_seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


def parse_utc_timestamp(timestamp: str) -> float:
    return float(calendar.timegm(time.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    tmp_path.replace(path)


@contextmanager
def exclusive_file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is not None:
        with lock_path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return

    lock_dir = lock_path.with_suffix(lock_path.suffix + ".dir")
    while True:  # pragma: no cover - fallback for non-POSIX hosts
        try:
            lock_dir.mkdir(parents=True)
            break
        except FileExistsError:
            time.sleep(0.05)
    try:
        yield
    finally:
        lock_dir.rmdir()


@contextmanager
def verifier_resource_lock(resource: str | None):
    if resource is None:
        yield
        return
    lock_root = Path(
        os.environ.get(
            VERIFIER_RESOURCE_LOCK_DIR_ENV,
            str(Path(tempfile.gettempdir()) / "goal-plus-verifier-locks"),
        )
    ).resolve()
    lock_name = f"{sha256_text(resource)}.lock"
    with exclusive_file_lock(lock_root / lock_name):
        yield


def path_matches(path: str, patterns: list[str]) -> bool:
    normalized = path.replace(os.sep, "/")
    for pattern in patterns:
        pat = pattern.replace(os.sep, "/")
        if normalized == pat or fnmatch(normalized, pat):
            return True
        if pat.endswith("/") and normalized.startswith(pat):
            return True
        if normalized.startswith(pat.rstrip("/") + "/"):
            return True
    return False


def safe_verifier_name(value: str) -> str:
    readable = "".join(
        character if character.isalnum() or character in {".", "_", "-"} else "_"
        for character in value
    ).strip("._-")
    return f"{readable or 'verifier'}-{sha256_text(value)[:8]}"


def relative_artifact_path(source_root: Path, artifact_path: Path) -> str:
    artifact = artifact_path.resolve()
    try:
        return artifact.relative_to(source_root.resolve()).as_posix()
    except ValueError:
        return artifact.name


def _normalize_verifier_cwds_for_candidate_workspace(spec: SearchSpec) -> SearchSpec:
    source_root = Path(spec.source_path).resolve()

    def normalize_command(command: VerifierCommand) -> VerifierCommand:
        cwd_path = Path(command.cwd)
        if cwd_path.resolve() == source_root:
            return command.model_copy(update={"cwd": "."})
        return command

    return spec.model_copy(
        deep=True,
        update={
            "process_verifiers": [
                normalize_command(command) for command in spec.process_verifiers
            ],
            "promotion_verifiers": [
                normalize_command(command) for command in spec.promotion_verifiers
            ],
        },
    )


class FileSearchRuntime:
    def __init__(
        self,
        root_dir: Path | str = DEFAULT_RUNTIME_ROOT,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.specs_dir = self.root_dir / "specs"
        self.runs_dir = self.root_dir / "runs"
        self.specs_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _execute_verifier_process(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        text: bool,
        capture_output: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        if not text or not capture_output:
            raise ValueError("verifier processes require text capture")

        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout_capture = _BoundedOutput()
        stderr_capture = _BoundedOutput()

        def drain(stream: Any, capture: _BoundedOutput) -> None:
            try:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        break
                    capture.append(chunk)
            except (OSError, ValueError):
                pass

        readers = [
            threading.Thread(
                target=drain,
                args=(process.stdout, stdout_capture),
                daemon=True,
            ),
            threading.Thread(
                target=drain,
                args=(process.stderr, stderr_capture),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_verifier_process_group(process)
            returncode = process.returncode if process.returncode is not None else -signal.SIGKILL

        for reader in readers:
            reader.join(timeout=VERIFIER_TERM_GRACE_SECONDS)
        if any(reader.is_alive() for reader in readers):
            # A verifier that exits while leaving descendants with inherited
            # output pipes would otherwise leak both processes and reader threads.
            self._terminate_verifier_process_group(process)
            for reader in readers:
                reader.join(timeout=VERIFIER_TERM_GRACE_SECONDS)
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

        stdout = stdout_capture.text()
        stderr = stderr_capture.text()
        if timed_out:
            raise subprocess.TimeoutExpired(
                command,
                timeout,
                output=stdout,
                stderr=stderr,
            )
        completed = subprocess.CompletedProcess(
            args=command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if check and returncode:
            raise subprocess.CalledProcessError(
                returncode,
                command,
                output=stdout,
                stderr=stderr,
            )
        return completed

    def _terminate_verifier_process_group(
        self,
        process: subprocess.Popen[bytes],
    ) -> None:
        if os.name != "posix":  # pragma: no cover - Windows fallback
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=VERIFIER_TERM_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            return

        process_group = process.pid

        def group_exists() -> bool:
            try:
                os.killpg(process_group, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            return True

        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            pass

        deadline = time.monotonic() + VERIFIER_TERM_GRACE_SECONDS
        while group_exists() and time.monotonic() < deadline:
            process.poll()
            time.sleep(0.02)
        if group_exists():
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=VERIFIER_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def freeze_spec(self, spec: SearchSpec, verifier_artifacts: list[Path]) -> FrozenSpec:
        spec = _normalize_verifier_cwds_for_candidate_workspace(spec)
        source_root = Path(spec.source_path).resolve()
        verifier_hashes: dict[str, str] = {}
        artifact_entries: list[tuple[Path, str]] = []

        for artifact in verifier_artifacts:
            artifact_path = Path(artifact).resolve()
            if not artifact_path.exists() or not artifact_path.is_file():
                raise FileNotFoundError(f"verifier artifact not found: {artifact_path}")
            try:
                artifact_path.relative_to(source_root)
            except ValueError as exc:
                raise ValueError(
                    f"Verifier artifact is outside source_path '{source_root}': "
                    f"{artifact_path}. Move it into a source-owned, materialized "
                    "path such as '.goal-plus-verifiers/'."
                ) from exc
            rel_path = relative_artifact_path(source_root, artifact_path)
            ignored_part = next(
                (part for part in Path(rel_path).parts if part in IGNORED_NAMES),
                None,
            )
            if ignored_part is not None:
                if ignored_part in {DEFAULT_RUNTIME_ROOT, LEGACY_RUNTIME_ROOT}:
                    raise ValueError(
                        "Verifier artifact is under the ignored Goal Plus runtime "
                        f"directory '{ignored_part}': {rel_path}. Move it to a "
                        "source-owned path such as "
                        "'.goal-plus-verifiers/score.sh'."
                    )
                raise ValueError(
                    f"Verifier artifact is under ignored workspace path "
                    f"'{ignored_part}': {rel_path}. Move it to a source-owned, "
                    "materialized path."
                )
            if Path(rel_path).suffix in IGNORED_SUFFIXES:
                raise ValueError(
                    f"Verifier artifact uses ignored workspace suffix "
                    f"'{Path(rel_path).suffix}': {rel_path}. Move it to a "
                    "source-owned, materialized path."
                )
            artifact_entries.append((artifact_path, rel_path))

        self._preflight_ranking_verifiers(spec)

        for artifact_path, rel_path in artifact_entries:
            verifier_hashes[rel_path] = sha256_file(artifact_path)

        spec_payload = spec.model_dump(mode="json")
        spec_hash = sha256_text(canonical_json({"spec": spec_payload, "verifiers": verifier_hashes}))
        frozen_spec_id = f"spec_{spec_hash[:12]}"
        spec_dir = self._spec_dir(frozen_spec_id)
        frozen_verifier_paths: dict[str, str] = {}

        for artifact_path, rel_path in artifact_entries:
            frozen_path = spec_dir / "frozen_verifiers" / rel_path
            frozen_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(artifact_path, frozen_path)
            frozen_verifier_paths[rel_path] = str(frozen_path)

        frozen = FrozenSpec(
            frozen_spec_id=frozen_spec_id,
            spec_hash=spec_hash,
            spec=spec,
            verifier_hashes=verifier_hashes,
            frozen_verifier_paths=frozen_verifier_paths,
            created_at=utc_timestamp(),
        )
        write_json(spec_dir / "frozen_spec.json", frozen.model_dump(mode="json"))
        return frozen

    def _preflight_ranking_verifiers(self, spec: SearchSpec) -> None:
        source_root = Path(spec.source_path).resolve()
        source_workspace = source_root if source_root.is_dir() else source_root.parent
        ranking_verifiers = [
            command
            for command in [*spec.process_verifiers, *spec.promotion_verifiers]
            if command.role == VerifierRole.RANKING_SIGNAL
        ]

        with tempfile.TemporaryDirectory(
            prefix="goal-plus-verifier-preflight-"
        ) as preflight_root:
            workspace = Path(preflight_root) / "workspace"
            copy_source_tree(source_workspace, workspace)
            initialize_workspace_git_baseline(workspace)

            for command in ranking_verifiers:
                cwd = (workspace / command.cwd).resolve()
                if not cwd.is_dir():
                    raise ValueError(
                        f"Ranking verifier '{command.name}' has a missing working "
                        f"directory: {cwd}"
                    )
                if command.command[0] == "goal-plus-internal":
                    raise ValueError(
                        f"Ranking verifier '{command.name}' cannot use a "
                        "goal-plus-internal command; use a process verifier that "
                        "prints the numeric metric as JSON."
                    )

                workspace_before = self._hash_verifier_workspace(workspace)
                try:
                    with verifier_resource_lock(command.resource_lock):
                        with tempfile.TemporaryDirectory(
                            prefix="goal-plus-verifier-command-"
                        ) as verifier_tmp:
                            verifier_tmp_path = Path(verifier_tmp)
                            diagnostics_dir = verifier_tmp_path / "diagnostics"
                            diagnostics_dir.mkdir()
                            completed = self._execute_verifier_process(
                                command.command,
                                cwd=cwd,
                                env=self._verifier_environment(
                                    cwd,
                                    verifier_tmp_path,
                                    phase="freeze_preflight",
                                    diagnostics_dir=diagnostics_dir,
                                    resource=command.resource_lock,
                                ),
                                text=True,
                                capture_output=True,
                                timeout=command.timeout_seconds,
                                check=False,
                            )
                except subprocess.TimeoutExpired as exc:
                    stdout = exc.stdout if isinstance(exc.stdout, str) else ""
                    stderr = exc.stderr if isinstance(exc.stderr, str) else ""
                    detail = _verifier_output_tail_detail(stdout, stderr)
                    raise ValueError(
                        f"Ranking verifier '{command.name}' timed out during freeze "
                        f"preflight after {command.timeout_seconds} seconds.{detail}"
                    ) from exc
                except OSError as exc:
                    raise ValueError(
                        f"Ranking verifier '{command.name}' could not start during "
                        f"freeze preflight: {exc}"
                    ) from exc

                side_effects = self._hash_changes(
                    workspace_before,
                    self._hash_verifier_workspace(workspace),
                )
                if side_effects:
                    raise ValueError(
                        f"VerifierWorkspaceSideEffect: ranking verifier "
                        f"'{command.name}' changed the disposable preflight "
                        f"workspace: {side_effects}. Verifiers must keep the "
                        "candidate workspace read-only. Put compiler products and "
                        "temporary outputs in the per-invocation directory exposed "
                        "through GOAL_PLUS_VERIFIER_TMPDIR/TMPDIR, or use Python "
                        "tempfile.TemporaryDirectory(). Never use one fixed /tmp "
                        "path because candidates may verify concurrently."
                    )

                if completed.returncode != 0:
                    detail = _verifier_output_tail_detail(
                        completed.stdout,
                        completed.stderr,
                    )
                    raise ValueError(
                        f"Ranking verifier '{command.name}' failed during freeze "
                        f"preflight with exit code {completed.returncode}.{detail}"
                    )

                metrics = self._parse_metrics(completed.stdout)
                if self._has_verifier_error(metrics):
                    error_detail = str(metrics["error"])[-2000:]
                    raise ValueError(
                        f"Ranking verifier '{command.name}' reported an error "
                        f"during freeze preflight: {error_detail}"
                    )
                score = self._score_from_metrics(spec.metric_name, metrics)
                if score is None:
                    example = canonical_json({spec.metric_name: 123.0})
                    raise ValueError(
                        f"Ranking verifier '{command.name}' exited successfully but "
                        "emitted no finite numeric metric. The final non-empty stdout "
                        f"line must be a JSON object such as: {example}. "
                        "VerifierCommand.expected_outputs lists artifact paths only; "
                        "it does not parse stdout."
                    )

    def _verifier_environment(
        self,
        cwd: Path,
        temp_dir: Path,
        *,
        phase: Literal["freeze_preflight", "candidate", "promotion"],
        diagnostics_dir: Path | None = None,
        resource: str | None = None,
    ) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(cwd) + os.pathsep + env.get("PYTHONPATH", "")
        for name in ("TMPDIR", "TMP", "TEMP", "GOAL_PLUS_VERIFIER_TMPDIR"):
            env[name] = str(temp_dir)
        env[VERIFIER_PHASE_ENV] = phase
        if diagnostics_dir is not None:
            env[VERIFIER_DIAGNOSTICS_ENV] = str(diagnostics_dir)
        else:
            env.pop(VERIFIER_DIAGNOSTICS_ENV, None)
        if resource is not None:
            env[VERIFIER_RESOURCE_ENV] = resource
        else:
            env.pop(VERIFIER_RESOURCE_ENV, None)
        return env

    def _hash_changes(
        self,
        before: dict[str, str],
        after: dict[str, str],
    ) -> list[str]:
        return [
            path
            for path in sorted(set(before) | set(after))
            if before.get(path) != after.get(path)
        ]

    def _hash_verifier_workspace(self, root: Path) -> dict[str, str]:
        hashes: dict[str, str] = {}
        ignored_names = IGNORED_NAMES - {".tmp"}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel_path = path.relative_to(root)
            if any(part in ignored_names for part in rel_path.parts):
                continue
            if path.suffix in IGNORED_SUFFIXES:
                continue
            hashes[rel_path.as_posix()] = sha256_file(path)
        return hashes

    def create_run(self, frozen_spec_id: str) -> str:
        frozen = self._load_frozen_spec(frozen_spec_id)
        run_id = f"run_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}_{uuid.uuid4().hex[:8]}"
        run = RunRecord(
            run_id=run_id,
            state=RunState.RUNNING,
            frozen_spec_id=frozen.frozen_spec_id,
            source_path=str(Path(frozen.spec.source_path).resolve()),
            created_at=utc_timestamp(),
        )
        self._write_run(run)
        (self._run_dir(run_id) / "candidates").mkdir(parents=True, exist_ok=True)
        (self._run_dir(run_id) / "workspace").mkdir(parents=True, exist_ok=True)
        (self._run_dir(run_id) / "plans").mkdir(parents=True, exist_ok=True)
        (self._run_dir(run_id) / "agent_sessions").mkdir(parents=True, exist_ok=True)
        return run_id

    def status(self, run_id: str) -> RunSummary:
        run = self._load_run(run_id)
        records = self._load_candidate_records(run_id)
        evaluated = sum(1 for record in records if record.status == "evaluated")
        return RunSummary(
            run_id=run.run_id,
            state=run.state,
            frozen_spec_id=run.frozen_spec_id,
            candidates_total=len(records),
            candidates_evaluated=evaluated,
            best_candidate_id=run.best_candidate_id,
            best_score=run.best_score,
            budget_used=run.budget_used,
        )

    def list_history(self, run_id: str, top_n: int = 5, sort_by: str = "score") -> dict[str, Any]:
        if top_n <= 0:
            raise ValueError("top_n must be > 0")
        if sort_by not in {"score", "created"}:
            raise ValueError("sort_by must be 'score' or 'created'")

        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        records = self._load_candidate_records(run_id)

        def score_value(record: CandidateRecord) -> float | None:
            return self._record_ranking_score(record, frozen.spec)

        def created_index(record: CandidateRecord) -> int:
            try:
                return int(record.candidate_id.removeprefix("c"))
            except ValueError:
                return 0

        if sort_by == "score":
            reverse = frozen.spec.metric_direction == "maximize"

            def score_key(record: CandidateRecord) -> tuple[int, float, int]:
                score = score_value(record)
                if score is None:
                    return (1, 0.0, created_index(record))
                sortable_score = score if reverse else -score
                return (0, -sortable_score, created_index(record))

            ordered = sorted(records, key=score_key)
        else:
            ordered = sorted(records, key=created_index)

        selected = ordered[:top_n]
        candidates = [
            self._history_candidate_payload(record, frozen.spec) for record in selected
        ]

        return {
            "run_id": run.run_id,
            "state": run.state,
            "frozen_spec_id": run.frozen_spec_id,
            "objective": frozen.spec.objective,
            "metric_name": frozen.spec.metric_name,
            "metric_direction": frozen.spec.metric_direction,
            "strategy": frozen.spec.strategy.model_dump(mode="json"),
            "worker_policy": self._normalize_worker_policy(frozen.spec.strategy),
            "best_candidate_id": run.best_candidate_id,
            "best_score": run.best_score,
            "total_candidates": len(records),
            "returned_candidates": len(candidates),
            "top_n": top_n,
            "sort_by": sort_by,
            "candidates": candidates,
        }

    def list_iterations(
        self,
        run_id: str,
        candidate_id: str,
    ) -> list[dict[str, Any]]:
        record = self._load_candidate_record(run_id, candidate_id)
        return [it.model_dump(mode="json") for it in record.iterations]

    def plan_next(self, run_id: str, requested_k: int = 4) -> SearchPlan:
        if requested_k <= 0:
            raise ValueError("requested_k must be > 0")

        run = self._load_run(run_id)
        if run.state not in {
            RunState.RUNNING,
            RunState.WAITING_FOR_WORKERS,
            RunState.SELECTING,
            RunState.SELECTION_BLOCKED,
        }:
            raise RuntimeError(f"cannot plan next batch from state {run.state}")

        frozen = self._load_frozen_spec(run.frozen_spec_id)
        spec = frozen.spec
        remaining = max(0, spec.budget.max_candidates - run.candidates_total)
        planned_k = min(requested_k, remaining, spec.budget.max_parallel)
        strategy = spec.strategy
        self._validate_host_strategy(strategy)
        mode = self._strategy_mode(strategy)

        if strategy.driver != "builtin":
            plan = self._plan_custom_strategy(run, frozen, requested_k, planned_k, remaining)
        elif mode in {"agent", "agent_guided", "default"}:
            plan = self._plan_agent_guided(run, frozen, requested_k, planned_k, remaining)
        elif mode in {"openevolve", "open_evolve", "openevolve_mode"}:
            plan = self._plan_openevolve(run, frozen, requested_k, planned_k, remaining)
        elif mode in {"evolve", "evolve_mode"}:
            plan = self._plan_evolve(run, frozen, requested_k, planned_k, remaining)
        elif mode in {"mcts", "mcts_mode"}:
            plan = self._plan_mcts(run, frozen, requested_k, planned_k, remaining)
        elif mode in {"random", "random_mode"}:
            plan = self._plan_random(run, frozen, requested_k, planned_k, remaining)
        elif mode in {"independent", "independent_branches"}:
            plan = self._plan_independent(run, frozen, requested_k, planned_k, remaining)
        else:
            raise ValueError(f"unknown builtin strategy: {strategy.name}")

        plan.worker_policy = self._normalize_worker_policy(plan.strategy, plan.worker_policy)
        plan.strategy_trace.setdefault("worker_policy", plan.worker_policy)
        self._write_plan(plan)
        run.budget_used["last_plan_id"] = plan.plan_id
        self._write_run(run)
        return plan

    def start_batch(
        self,
        run_id: str,
        plan_id: str,
        proposals: list[CandidateProposal] | None = None,
    ) -> list[CandidateTask]:
        with self._run_transaction(run_id):
            return self._start_batch_locked(run_id, plan_id, proposals)

    def _start_batch_locked(
        self,
        run_id: str,
        plan_id: str,
        proposals: list[CandidateProposal] | None,
    ) -> list[CandidateTask]:
        run = self._load_run(run_id)
        if run.state not in {
            RunState.RUNNING,
            RunState.WAITING_FOR_WORKERS,
            RunState.SELECTING,
            RunState.SELECTION_BLOCKED,
        }:
            raise RuntimeError(f"cannot create candidates from state {run.state}")

        frozen = self._load_frozen_spec(run.frozen_spec_id)
        plan = self._load_plan(run_id, plan_id)
        all_records = self._load_candidate_records(run_id)
        plan_records = sorted(
            (record for record in all_records if record.task.plan_id == plan_id),
            key=lambda record: record.candidate_id,
        )
        for record in plan_records:
            self._write_candidate_record(run_id, record)
        if all_records:
            highest_index = max(
                int(record.candidate_id.removeprefix("c")) for record in all_records
            )
            run.candidates_total = max(run.candidates_total, len(all_records))
            run.next_candidate_index = max(run.next_candidate_index, highest_index + 1)

        if plan.status == "started":
            records_by_id = {record.candidate_id: record for record in plan_records}
            try:
                tasks = [
                    records_by_id[candidate_id].task
                    for candidate_id in plan.started_candidate_ids
                ]
            except KeyError as exc:
                raise RuntimeError(
                    f"started plan {plan_id} is missing candidate state for {exc.args[0]}"
                ) from exc
            if tasks:
                run.state = RunState.WAITING_FOR_WORKERS
                self._write_run(run)
            return tasks
        if plan.status != "planned":
            raise RuntimeError(f"plan {plan_id} has already been started")

        remaining = max(0, frozen.spec.budget.max_candidates - run.candidates_total)
        target_count = min(plan.planned_k, len(plan_records) + remaining)
        if target_count <= 0:
            return []

        if plan.requires_agent_proposals:
            if not proposals:
                raise ValueError("this strategy plan requires candidate proposals")
            self._validate_agent_proposals(plan, proposals)
            candidate_proposals = proposals[:target_count]
        else:
            if proposals:
                raise ValueError("this strategy plan already contains fixed work orders")
            candidate_proposals = [
                self._proposal_from_work_order(work_order) for work_order in plan.work_orders
            ][:target_count]

        if len(plan_records) > len(candidate_proposals):
            raise RuntimeError(
                f"plan {plan_id} has more persisted candidates than candidate proposals"
            )
        for record, proposal in zip(plan_records, candidate_proposals, strict=False):
            if record.task.proposal != proposal:
                raise RuntimeError(
                    f"retry proposals do not match persisted candidate "
                    f"{record.candidate_id}"
                )

        tasks = [record.task for record in plan_records]
        for index, proposal in enumerate(
            candidate_proposals[len(plan_records):], start=len(plan_records) + 1
        ):
            candidate_id = f"c{run.next_candidate_index:03d}"
            task = self._create_candidate_task(
                run=run,
                frozen=frozen,
                candidate_id=candidate_id,
                plan=plan,
                proposal=proposal,
                slot=index,
            )
            record = CandidateRecord(candidate_id=candidate_id, status="created", task=task)
            self._write_candidate_record(run_id, record)
            tasks.append(task)
            run.next_candidate_index += 1
            run.candidates_total += 1
            run.state = RunState.WAITING_FOR_WORKERS
            self._write_run(run)

        if tasks:
            run.state = RunState.WAITING_FOR_WORKERS
            plan.status = "started"
            plan.started_candidate_ids = [task.candidate_id for task in tasks]
            self._write_plan(plan)
            self._write_run(run)

        return tasks

    def start_agent_session(
        self,
        run_id: str,
        candidate_id: str,
        directive: dict[str, Any] | str | None = None,
        *,
        worker_budget: dict[str, Any] | None = None,
    ) -> AgentSessionRecord:
        """Create a context/provenance handle and host-native launch payload.

        Does not start a worker or track lifecycle state. ``worker_budget`` is
        an optional one-dispatch override; it does not mutate the frozen spec or
        the candidate policy.
        """
        return self._create_agent_session(
            run_id=run_id,
            candidate_id=candidate_id,
            directive=directive,
            worker_budget_override=worker_budget,
        )

    def redispatch_candidate(
        self,
        run_id: str,
        candidate_id: str,
        directive: dict[str, Any] | str | None = None,
        *,
        worker_agent_type: str | None = None,
        worker_budget: dict[str, Any] | None = None,
    ) -> AgentSessionRecord:
        """Create a new worker launch for an existing candidate workspace.

        This is state-level resume, not same-worker continuation. It allocates
        a new agent_session_id for the same candidate/workspace and may
        temporarily override the worker tier or budget for that launch. It does
        not mutate the candidate task policy or track host lifecycle state.
        """
        if worker_agent_type is not None and not worker_agent_type.strip():
            raise ValueError("worker_agent_type must be non-empty when provided")

        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        candidate_record = self._load_candidate_record(run_id, candidate_id)
        if candidate_record.status not in {"created", "evaluated"}:
            raise RuntimeError(
                f"cannot redispatch candidate in status {candidate_record.status}"
            )

        selected_worker_agent_type = (
            worker_agent_type
            or self._candidate_worker_agent_type(frozen, candidate_record)
        )
        worker_budget_override = self._resolve_worker_budget_for_dispatch(
            frozen=frozen,
            candidate_record=candidate_record,
            worker_agent_type=selected_worker_agent_type,
            worker_budget_override=worker_budget,
        )
        normalized_directive = self._normalize_main_directive(directive)
        previous_session_ids = [
            session["agent_session_id"]
            for session in self._agent_session_payloads_for_candidate(run_id, candidate_id)
        ]
        resume_directive = {
            **normalized_directive,
            "state_level_resume": True,
            "resume_candidate_id": candidate_id,
            "previous_agent_session_ids": previous_session_ids,
            "resume_instruction": (
                "This is a new worker session for an existing candidate. "
                "Call search_get_agent_context first and use its history and "
                "iterations as the authoritative resume context."
            ),
        }
        return self._create_agent_session(
            run_id=run_id,
            candidate_id=candidate_id,
            directive=resume_directive,
            worker_agent_type_override=selected_worker_agent_type,
            worker_budget_override=worker_budget_override,
        )

    def _create_agent_session(
        self,
        *,
        run_id: str,
        candidate_id: str,
        directive: dict[str, Any] | str | None,
        worker_agent_type_override: str | None = None,
        worker_budget_override: dict[str, Any] | None = None,
    ) -> AgentSessionRecord:
        with self._run_transaction(run_id):
            run = self._load_run(run_id)
            if run.state not in {
                RunState.RUNNING,
                RunState.WAITING_FOR_WORKERS,
                RunState.SELECTING,
                RunState.SELECTION_BLOCKED,
            }:
                raise RuntimeError(f"cannot start agent session from state {run.state}")
            frozen = self._load_frozen_spec(run.frozen_spec_id)

            candidate_record = self._load_candidate_record(run_id, candidate_id)
            workspace = candidate_record.task.workspace

            if worker_budget_override is not None:
                selected_worker_agent_type = (
                    worker_agent_type_override
                    or self._candidate_worker_agent_type(frozen, candidate_record)
                )
                worker_budget_override = self._normalize_worker_budget_override(
                    worker_host=frozen.spec.strategy.worker_host,
                    worker_agent_type=selected_worker_agent_type,
                    worker_budget=worker_budget_override,
                )

            agent_session_id = self._make_agent_session_id(run_id, run.next_agent_session_index)
            run.next_agent_session_index += 1
            now = utc_timestamp()
            normalized_directive = self._normalize_main_directive(directive)
            launch = self._build_launch_payload(
                frozen=frozen,
                candidate_id=candidate_id,
                agent_session_id=agent_session_id,
                directive=normalized_directive,
                candidate_record=candidate_record,
                worker_agent_type_override=worker_agent_type_override,
                worker_budget_override=worker_budget_override,
            )
            host = frozen.spec.strategy.worker_host
            if host == "pi-rpc":
                launch["run_id"] = run_id
            host_handle = AgentHostHandle(host=host)
            if host == "codex":
                host_handle = host_handle.model_copy(
                    update={"task_name": launch.get("task_name")}
                )
            elif host == "pi-rpc":
                host_handle = host_handle.model_copy(
                    update={
                        "external_id": launch.get("session_id", agent_session_id),
                        "metadata": {"continuation": "state_redispatch"},
                    }
                )
            session = AgentSessionRecord(
                agent_session_id=agent_session_id,
                run_id=run_id,
                candidate_id=candidate_id,
                host=host,
                host_handle=host_handle,
                created_at=now,
                updated_at=now,
                directive=normalized_directive,
                workspace=workspace,
                launch=launch,
                counters={},
            )
            self._write_run(run)
            self._write_agent_session(session)
            return session

    def bind_opencode_session(
        self,
        agent_session_id: str,
        opencode_session_id: str,
    ) -> AgentSessionRecord:
        """Bind a runtime agent session to the OpenCode session created by Task.

        The runtime cannot observe OpenCode's Task return value by itself, so
        the main agent records the returned `metadata.sessionId` here. This
        mapping is what lets a later turn continue the same OpenCode session
        via Task(task_id=...).
        """
        session = self._load_agent_session_by_id(agent_session_id)
        bound_id = opencode_session_id.strip()
        if not bound_id:
            raise ValueError("opencode_session_id must be non-empty")
        existing_id = session.opencode_session_id or session.host_handle.external_id
        if existing_id and existing_id != bound_id:
            raise ValueError(
                "agent session is already bound to a different OpenCode session"
            )
        if session.host != "opencode":
            raise ValueError(f"agent session host is {session.host}, not opencode")

        return self.bind_agent_handle(
            agent_session_id,
            {"host": "opencode", "external_id": bound_id},
        )

    def bind_agent_handle(
        self,
        agent_session_id: str,
        handle: dict[str, Any],
    ) -> AgentSessionRecord:
        """Bind a runtime session to the host-specific worker handle."""
        session = self._load_agent_session_by_id(agent_session_id)
        host = handle.get("host", session.host)
        if host != session.host:
            raise ValueError(f"agent session host is {session.host}, got handle for {host}")

        metadata = {
            **session.host_handle.metadata,
            **dict(handle.get("metadata") or {}),
        }
        updated_handle = session.host_handle.model_copy(
            update={
                "host": session.host,
                "external_id": handle.get("external_id", session.host_handle.external_id),
                "task_name": handle.get("task_name", session.host_handle.task_name),
                "nickname": handle.get("nickname", session.host_handle.nickname),
                "metadata": metadata,
            }
        )
        update: dict[str, Any] = {
            "host_handle": updated_handle,
            "updated_at": utc_timestamp(),
        }
        if session.host == "opencode" and updated_handle.external_id:
            update["opencode_session_id"] = updated_handle.external_id
        updated = session.model_copy(update=update)
        self._write_agent_session(updated)
        return updated

    def continue_agent_session(
        self,
        agent_session_id: str,
        directive: dict[str, Any] | str | None = None,
        worker_budget: dict[str, Any] | None = None,
    ) -> AgentSessionRecord:
        """Return host launch fields that continue a prior worker session.

        This does not create a new candidate workspace. Hosts with native
        continuation reuse the bound worker; state-redispatch hosts return
        their explicit redispatch payload. ``worker_budget`` applies only to
        this continuation dispatch and does not mutate the frozen spec.
        """
        session = self._load_agent_session_by_id(agent_session_id)
        if session.host == "opencode" and not (
            session.opencode_session_id or session.host_handle.external_id
        ):
            raise RuntimeError(
                "agent session has no bound OpenCode session id; call "
                "search_bind_opencode_session with Task metadata.sessionId first"
            )

        run = self._load_run(session.run_id)
        if run.state not in {
            RunState.RUNNING,
            RunState.WAITING_FOR_WORKERS,
            RunState.SELECTING,
            RunState.SELECTION_BLOCKED,
        }:
            raise RuntimeError(f"cannot continue agent session from state {run.state}")
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        candidate_record = self._load_candidate_record(
            session.run_id,
            session.candidate_id,
        )
        if candidate_record.status not in {"created", "evaluated"}:
            raise RuntimeError(
                f"cannot continue candidate in status {candidate_record.status}"
            )

        normalized_directive = (
            session.directive
            if directive is None
            else self._normalize_main_directive(directive)
        )
        worker_agent_type = self._candidate_worker_agent_type(
            frozen,
            candidate_record,
        )
        worker_budget_override = self._normalize_worker_budget_override(
            worker_host=session.host,
            worker_agent_type=worker_agent_type,
            worker_budget=worker_budget,
        )
        try:
            launch = self._build_continue_launch_payload(
                frozen=frozen,
                session=session,
                directive=normalized_directive,
                candidate_record=candidate_record,
                worker_budget_override=worker_budget_override,
            )
        except UnsupportedHostCapability as exc:
            raise RuntimeError(str(exc)) from exc
        updated = session.model_copy(
            update={
                "updated_at": utc_timestamp(),
                "directive": normalized_directive,
                "workspace": candidate_record.task.workspace,
                "launch": launch,
            }
        )
        self._write_agent_session(updated)
        return updated

    def get_agent_context(self, agent_session_id: str) -> dict[str, Any]:
        """Subagent first call. Returns the authoritative ids, workspace, and
        candidate context. The subagent must treat prompt-supplied ids as
        labels only and rely on this response as the source of truth.
        """
        session = self._load_agent_session_by_id(agent_session_id)
        run = self._load_run(session.run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        candidate_record = self._load_candidate_record(session.run_id, session.candidate_id)
        previous_sessions: list[dict[str, Any]] = []
        latest_handoff: dict[str, Any] | None = None
        for previous in self._load_agent_sessions(session.run_id):
            if (
                previous.candidate_id != session.candidate_id
                or previous.agent_session_id == session.agent_session_id
            ):
                continue
            metadata = previous.host_handle.metadata
            progress_handoff = metadata.get("progress_handoff")
            if isinstance(progress_handoff, dict):
                latest_handoff = progress_handoff
            assistant_text = metadata.get("assistant_text")
            error = metadata.get("error")
            previous_sessions.append(
                {
                    "agent_session_id": previous.agent_session_id,
                    "timed_out": bool(metadata.get("timed_out")),
                    "runner_failed": bool(metadata.get("runner_failed")),
                    "assistant_summary": (
                        assistant_text[:2000] + ("..." if len(assistant_text) > 2000 else "")
                        if isinstance(assistant_text, str)
                        else None
                    ),
                    "progress_handoff": progress_handoff
                    if isinstance(progress_handoff, dict)
                    else None,
                    "error": (
                        error[:500] + ("..." if len(error) > 500 else "")
                        if isinstance(error, str)
                        else None
                    ),
                }
            )
        workspace_status = self._git_status(candidate_record.task.workspace)
        return {
            "agent_session_id": session.agent_session_id,
            "run_id": session.run_id,
            "candidate_id": session.candidate_id,
            "host": session.host,
            "host_handle": session.host_handle.model_dump(mode="json"),
            "directive": session.directive,
            "workspace": str(session.workspace),
            "objective": frozen.spec.objective,
            "metric_name": frozen.spec.metric_name,
            "metric_direction": frozen.spec.metric_direction,
            "run_budget": frozen.spec.budget.model_dump(mode="json"),
            "candidate_task": candidate_record.task.model_dump(mode="json"),
            "resume": {
                "is_redispatch": bool(session.directive.get("state_level_resume")),
                "previous_sessions": previous_sessions,
                "latest_handoff": latest_handoff,
                "workspace": {
                    "git_head": self._git_head(candidate_record.task.workspace),
                    "git_status": workspace_status,
                    "dirty": bool(workspace_status),
                    "changed_files": self._detect_changed_files(
                        Path(run.source_path), candidate_record.task.workspace
                    ),
                },
            },
            "history": self.list_history(session.run_id, top_n=5, sort_by="score"),
            "iterations": self.list_iterations(session.run_id, session.candidate_id),
        }

    def run_verifier(
        self,
        run_id: str,
        candidate_id: str,
        scope: Literal["process", "promotion"] = "process",
        agent_session_id: str | None = None,
    ) -> ScoreReport:
        """Subagent self-score with ``agent_session_id``; main final verify
        without it. Process calls record ranking iterations; promotion calls
        retain separate acceptance evidence.
        """
        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        record = self._load_candidate_record(run_id, candidate_id)
        if record.status not in {"created", "evaluated"}:
            raise RuntimeError(
                f"cannot verify candidate in status {record.status}"
            )
        if scope == "promotion":
            if agent_session_id is not None:
                raise PermissionError(
                    "promotion verification is parent-owned and cannot be "
                    "called from a candidate agent session"
                )
            if (
                run.state != RunState.READY_TO_PROMOTE
                or run.selected_candidate_id != candidate_id
                or not run.selected_git_head
            ):
                raise RuntimeError(
                    "promotion verification requires the candidate and immutable "
                    "Git revision selected by search_select"
                )

        session: AgentSessionRecord | None = None
        if agent_session_id:
            session = self._load_agent_session_by_id(agent_session_id, run_id=run_id)
            if session.candidate_id != candidate_id:
                raise ValueError(
                    "agent_session_id does not belong to this candidate"
                )

        detected_changed = self._detect_changed_files(
            Path(run.source_path), record.task.workspace
        )
        touched_denied = any(
            path_matches(path, frozen.spec.edit_surface.deny) for path in detected_changed
        )
        outside_allowed = any(
            not path_matches(path, frozen.spec.edit_surface.allow) for path in detected_changed
        )
        if (
            frozen.spec.edit_surface.max_file_changes is not None
            and len(detected_changed) > frozen.spec.edit_surface.max_file_changes
        ):
            outside_allowed = True

        record.detected_changed_files = detected_changed
        record.touched_denied_files = touched_denied
        record.changed_outside_allowed = outside_allowed

        try:
            precheck = self._precheck_candidate(frozen, record)
            if precheck is not None:
                report = precheck
            else:
                self._commit_workspace_iteration(
                    record.task.workspace,
                    detected_changed,
                    (
                        f"search verifier iteration "
                        f"{candidate_id}:{len(record.iterations) + 1}"
                    ),
                )
                commands = (
                    frozen.spec.process_verifiers
                    if scope == "process"
                    else frozen.spec.promotion_verifiers
                )
                if not commands:
                    commands = frozen.spec.process_verifiers
                report = self._run_commands(run, frozen, record, commands, scope)

            if scope == "promotion" and report.promotion_passed is None:
                report = report.model_copy(
                    update={"promotion_passed": report.process_passed}
                )

            detected_changed = self._detect_changed_files(
                Path(run.source_path), record.task.workspace
            )
            artifact_hash = self._artifact_hash(
                record.task.workspace, detected_changed
            )
            git_head = self._git_head(record.task.workspace)
            git_status = self._git_status(record.task.workspace)
            git_artifact_clean = self._git_artifact_clean(
                record.task.workspace,
                detected_changed,
                git_head,
            )
            touched_denied = any(
                path_matches(path, frozen.spec.edit_surface.deny)
                for path in detected_changed
            )
            outside_allowed = any(
                not path_matches(path, frozen.spec.edit_surface.allow)
                for path in detected_changed
            )
            if (
                frozen.spec.edit_surface.max_file_changes is not None
                and len(detected_changed)
                > frozen.spec.edit_surface.max_file_changes
            ):
                outside_allowed = True

            with self._run_transaction(run_id):
                run = self._load_run(run_id)
                record = self._load_candidate_record(run_id, candidate_id)
                record.detected_changed_files = detected_changed
                record.touched_denied_files = touched_denied
                record.changed_outside_allowed = outside_allowed
                if scope == "process":
                    record.status = "evaluated"
                    record.score_report = report
                    if record.promotion_evidence and (
                        record.promotion_evidence.git_head != git_head
                        or record.promotion_evidence.artifact_hash != artifact_hash
                    ):
                        record.promotion_report = None
                        record.promotion_evidence = None
                    record.iterations.append(
                        IterationRecord(
                            iteration=len(record.iterations) + 1,
                            agent_session_id=agent_session_id,
                            score=report.aggregate_score,
                            process_passed=report.process_passed,
                            git_head=git_head,
                            git_artifact_clean=git_artifact_clean,
                            git_status=git_status,
                            failure_class=(
                                next(
                                    (
                                        r.failure_class
                                        for r in report.verifier_results
                                        if r.failure_class
                                    ),
                                    None,
                                )
                            ),
                            summary="",
                            changed_files=list(detected_changed),
                            touched_denied_files=touched_denied,
                            changed_outside_allowed=outside_allowed,
                            artifact_hash=artifact_hash,
                            metrics={
                                r.name: r.metrics
                                for r in report.verifier_results
                            },
                            created_at=utc_timestamp(),
                        )
                    )
                else:
                    record.promotion_report = report
                    record.promotion_evidence = PromotionEvidence(
                        candidate_id=candidate_id,
                        selected_git_head=run.selected_git_head,
                        git_head=git_head,
                        artifact_hash=artifact_hash,
                        passed=bool(report.promotion_passed),
                        created_at=utc_timestamp(),
                    )
                self._write_candidate_record(run_id, record)
                if scope == "process":
                    self._update_best_seen(run, frozen.spec, report)
                    run.candidates_evaluated = len(
                        [
                            r
                            for r in self._load_candidate_records(run_id)
                            if r.status == "evaluated"
                        ]
                    )
                self._write_run(run)

                if session is not None and agent_session_id is not None:
                    latest_session = self._load_agent_session_by_id(
                        agent_session_id, run_id=run_id
                    )
                    counters = dict(latest_session.counters)
                    counters["verifier_runs"] = counters.get("verifier_runs", 0) + 1
                    updated = latest_session.model_copy(
                        update={
                            "updated_at": utc_timestamp(),
                            "counters": counters,
                        }
                    )
                    self._write_agent_session(updated)

            return report
        except Exception:
            if scope == "process":
                with self._run_transaction(run_id):
                    run = self._load_run(run_id)
                    run.state = RunState.FAILED
                    self._write_run(run)
            raise

    def select(self, run_id: str, strategy: str = "independent_branches") -> dict[str, Any]:
        run = self._load_run(run_id)
        if run.state not in {
            RunState.RUNNING,
            RunState.WAITING_FOR_WORKERS,
            RunState.SELECTING,
            RunState.SELECTION_BLOCKED,
            RunState.READY_TO_PROMOTE,
        }:
            raise RuntimeError(f"cannot select candidate from state {run.state}")
        run.state = RunState.SELECTING
        run.budget_used.pop("selection_blocked_reason", None)
        self._write_run(run)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        records = self._load_candidate_records(run_id)
        options = self._selection_options(run, records, frozen.spec.metric_direction)
        if not options:
            self._mark_selection_blocked(
                run_id,
                "no verifier-backed candidate iteration is eligible for selection",
            )
            raise RuntimeError("no verified candidates available for selection")

        reverse = frozen.spec.metric_direction == "maximize"
        ranked = sorted(options, key=lambda item: item[0], reverse=reverse)
        selected_score: float | None = None
        selected_record: CandidateRecord | None = None
        selected_iteration: int | None = None
        selected_git_head: str | None = None
        final_report: ScoreReport | None = None
        for option_score, option_record, option_iteration, option_git_head in ranked:
            if option_git_head:
                self._checkout_git_revision(option_record.task.workspace, option_git_head)
            report = self.run_verifier(run_id, option_record.candidate_id)
            if report.process_passed and report.aggregate_score is not None:
                selected_score = report.aggregate_score
                selected_record = option_record
                selected_iteration = option_iteration
                selected_git_head = option_git_head
                final_report = report
                break

        if selected_record is None or selected_score is None:
            self._mark_selection_blocked(
                run_id,
                "all eligible candidate revisions failed final verification",
            )
            raise RuntimeError("no selected candidate passed final verification")

        selected_changed_files = self._detect_changed_files(
            Path(run.source_path), selected_record.task.workspace
        )
        selected_artifact_hash = self._artifact_hash(
            selected_record.task.workspace,
            selected_changed_files,
        )
        run = self._load_run(run_id)
        run.state = RunState.READY_TO_PROMOTE
        run.selected_candidate_id = selected_record.candidate_id
        run.best_candidate_id = selected_record.candidate_id
        run.best_score = selected_score
        run.selected_score = selected_score
        run.selected_iteration = selected_iteration
        run.selected_git_head = selected_git_head
        run.selected_artifact_hash = selected_artifact_hash
        run.budget_used.pop("selection_blocked_reason", None)
        self._write_run(run)
        selected_record = self._load_candidate_record(
            run_id, selected_record.candidate_id
        )
        selected_record.promotion_report = None
        selected_record.promotion_evidence = None
        self._write_candidate_record(run_id, selected_record)
        return {
            "strategy": strategy,
            "selected_candidate_id": selected_record.candidate_id,
            "selected_score": selected_score,
            "selected_iteration": selected_iteration,
            "selected_git_head": selected_git_head,
            "selected_artifact_hash": selected_artifact_hash,
            "selection_basis_score": (
                next(
                    (
                        score
                        for score, record, iteration, git_head in ranked
                        if record.candidate_id == selected_record.candidate_id
                        and iteration == selected_iteration
                        and git_head == selected_git_head
                    ),
                    selected_score,
                )
            ),
            "final_verifier_score": final_report.aggregate_score if final_report else None,
            "best_candidate_id": run.best_candidate_id,
            "best_score": run.best_score,
        }

    def _mark_selection_blocked(self, run_id: str, reason: str) -> None:
        run = self._load_run(run_id)
        run.state = RunState.SELECTION_BLOCKED
        run.budget_used["selection_blocked_reason"] = reason
        self._write_run(run)

    def report(self, run_id: str) -> Path:
        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        records = self._load_candidate_records(run_id)
        plans = self._load_plans(run_id)
        report_path = self._run_dir(run_id) / "report.md"

        lines = [
            f"# Search Report: {run_id}",
            "",
            f"- Frozen spec: `{frozen.frozen_spec_id}`",
            f"- Spec hash: `{frozen.spec_hash}`",
            f"- Objective: {frozen.spec.objective}",
            f"- Metric: `{frozen.spec.metric_name}` ({frozen.spec.metric_direction})",
            f"- Strategy: `{frozen.spec.strategy.name}` ({frozen.spec.strategy.driver})",
            f"- Best candidate: `{run.best_candidate_id}`",
            f"- Best score: `{run.best_score}`",
            f"- Selected score: `{run.selected_score}`",
            f"- Selected iteration: `{run.selected_iteration}`",
            f"- Selected git head: `{run.selected_git_head}`",
            "",
            "## Strategy Plans",
            "",
            "| Plan | Status | Strategy | Worker Mode | Requested | Planned | Started Candidates | Trace |",
            "|---|---|---|---|---:|---:|---|---|",
        ]
        for plan in plans:
            trace = plan.strategy_trace.get("reason") or plan.strategy_trace.get("selection_rule") or ""
            lines.append(
                f"| `{plan.plan_id}` | {plan.status} | `{plan.strategy.name}` | "
                f"`{plan.worker_policy.get('mode', plan.strategy.worker_mode)}` | "
                f"{plan.requested_k} | {plan.planned_k} | "
                f"{self._markdown_cell(', '.join(plan.started_candidate_ids))} | "
                f"{self._markdown_cell(str(trace))} |"
            )
        lines.extend(
            [
                "",
                "## Candidates",
                "",
                "| Candidate | Plan | Agent Sessions | Parent/Base | Status | Score | Git Head | Best Iteration | Best Score | Best Git Head | Process | Summary | Key Metrics | Changed Files |",
                "|---|---|---|---|---|---:|---|---:|---:|---|---|---|---|---|",
            ]
        )
        for record in records:
            score = ""
            passed = ""
            latest_iteration = record.iterations[-1] if record.iterations else None
            git_head = latest_iteration.git_head if latest_iteration else ""
            if record.score_report:
                score = "" if record.score_report.aggregate_score is None else str(record.score_report.aggregate_score)
                passed = str(record.score_report.process_passed)
            payload = self._history_candidate_payload(record, frozen.spec)
            key_metrics = ", ".join(
                f"{key}={value}" for key, value in payload["key_metrics"].items()
            )
            changed = ", ".join(record.detected_changed_files)
            agent_sessions = ", ".join(
                session["agent_session_id"] for session in payload["agent_sessions"]
            )
            best_iteration = self._best_iteration_record(record, frozen.spec.metric_direction)
            best_iteration_value = (
                "" if best_iteration is None else str(best_iteration.iteration)
            )
            best_score_value = (
                ""
                if best_iteration is None or best_iteration.score is None
                else str(best_iteration.score)
            )
            best_git_head = "" if best_iteration is None else best_iteration.git_head or ""
            parent_base = ", ".join(
                part
                for part in [
                    f"parent={record.task.parent_id}" if record.task.parent_id else "",
                    f"base={record.task.base_candidate_id}" if record.task.base_candidate_id else "",
                ]
                if part
            )
            lines.append(
                f"| `{record.candidate_id}` | `{record.task.plan_id or ''}` | "
                f"{self._markdown_cell(agent_sessions)} | "
                f"{self._markdown_cell(parent_base)} | {record.status} | {score} | "
                f"{self._markdown_cell(git_head or '')} | "
                f"{best_iteration_value} | {best_score_value} | "
                f"{self._markdown_cell(best_git_head)} | {passed} | "
                f"{self._markdown_cell(payload['summary'])} | "
                f"{self._markdown_cell(key_metrics)} | {self._markdown_cell(changed)} |"
            )
        agent_sessions = self._load_agent_sessions(run_id)
        if agent_sessions:
            session_rows = [
                (session, self._display_host_handle(session)) for session in agent_sessions
            ]
            include_handle = any(
                handle and handle != session.agent_session_id
                for session, handle in session_rows
            )
            lines.extend(
                [
                    "",
                    "## Agent Sessions",
                    "",
                ]
            )
            if include_handle:
                lines.extend(
                    [
                        "| Session | Host | Handle | Candidate | Verifier Runs | Created | Updated |",
                        "|---|---|---|---|---:|---|---|",
                    ]
                )
            else:
                lines.extend(
                    [
                        "| Session | Host | Candidate | Verifier Runs | Created | Updated |",
                        "|---|---|---|---:|---|---|",
                    ]
                )
            for session, handle in session_rows:
                common = (
                    f"| `{session.agent_session_id}` | "
                    f"`{session.host}` | "
                )
                if include_handle:
                    display_handle = handle if handle != session.agent_session_id else ""
                    common += f"{self._markdown_cell(display_handle)} | "
                lines.append(
                    common
                    + f"`{session.candidate_id or ''}` | "
                    f"{session.counters.get('verifier_runs', 0)} | "
                    f"{session.created_at} | {session.updated_at} |"
                )
        lines.append("")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path

    def promote(self, run_id: str, candidate_id: str) -> Path:
        run = self._load_run(run_id)
        if run.selected_candidate_id != candidate_id:
            raise RuntimeError(
                "cannot promote candidate before search_select selects it"
            )
        frozen = self._load_frozen_spec(run.frozen_spec_id)

        def reject_promotion(message: str) -> None:
            latest_run = self._load_run(run_id)
            latest_run.state = RunState.READY_TO_PROMOTE
            self._write_run(latest_run)
            raise RuntimeError(message)

        record = self._load_candidate_record(run_id, candidate_id)
        if not run.selected_git_head:
            reject_promotion(
                "cannot promote candidate without an immutable selected Git revision"
            )
        self._checkout_git_revision(record.task.workspace, run.selected_git_head)
        detected_changed = self._detect_changed_files(
            Path(run.source_path), record.task.workspace
        )
        artifact_hash = self._artifact_hash(
            record.task.workspace, detected_changed
        )
        git_head = self._git_head(record.task.workspace)
        record.detected_changed_files = detected_changed
        record.touched_denied_files = any(
            path_matches(path, frozen.spec.edit_surface.deny)
            for path in detected_changed
        )
        record.changed_outside_allowed = any(
            not path_matches(path, frozen.spec.edit_surface.allow)
            for path in detected_changed
        )
        if (
            frozen.spec.edit_surface.max_file_changes is not None
            and len(detected_changed) > frozen.spec.edit_surface.max_file_changes
        ):
            record.changed_outside_allowed = True
        self._write_candidate_record(run_id, record)

        if run.selected_git_head and git_head != run.selected_git_head:
            reject_promotion(
                "cannot promote candidate because the selected Git revision is stale"
            )
        if run.selected_artifact_hash is None:
            run.selected_artifact_hash = artifact_hash
            self._write_run(run)
        elif artifact_hash != run.selected_artifact_hash:
            reject_promotion(
                "cannot promote candidate because the selected artifact changed"
            )
        if not record.score_report or not record.score_report.process_passed:
            reject_promotion(
                "cannot promote candidate without a passing score report"
            )
        if record.touched_denied_files or record.changed_outside_allowed:
            reject_promotion(
                "cannot promote candidate that changed denied/out-of-surface files"
            )

        if frozen.spec.promotion_verifiers:
            promotion_report = self.run_verifier(
                run_id,
                candidate_id,
                scope="promotion",
            )
            run = self._load_run(run_id)
            record = self._load_candidate_record(run_id, candidate_id)
            detected_changed = self._detect_changed_files(
                Path(run.source_path), record.task.workspace
            )
            artifact_hash = self._artifact_hash(
                record.task.workspace, detected_changed
            )
            git_head = self._git_head(record.task.workspace)
            evidence = record.promotion_evidence
            evidence_is_current = bool(
                evidence
                and evidence.candidate_id == candidate_id
                and evidence.selected_git_head == run.selected_git_head
                and evidence.git_head == git_head
                and evidence.artifact_hash == artifact_hash
                and evidence.artifact_hash == run.selected_artifact_hash
                and evidence.passed
            )
            if not promotion_report.promotion_passed or not evidence_is_current:
                reject_promotion(
                    "cannot promote candidate without fresh passing promotion evidence"
                )

        promotion_dir = self._run_dir(run_id) / "promotion"
        promotion_dir.mkdir(parents=True, exist_ok=True)
        patch_path = promotion_dir / f"{candidate_id}.patch"
        self._write_patch(
            Path(run.source_path),
            record.task.workspace,
            run.selected_git_head,
            detected_changed,
            patch_path,
        )
        run.state = RunState.PROMOTED
        run.selected_candidate_id = candidate_id
        self._write_run(run)
        return patch_path

    def _strategy_mode(self, strategy: StrategySpec) -> str:
        return strategy.name.strip().lower().replace("-", "_")

    def _display_host_handle(self, session: AgentSessionRecord) -> str:
        return (
            session.host_handle.external_id
            or session.host_handle.task_name
            or session.host_handle.nickname
            or session.opencode_session_id
            or ""
        )

    def _validate_host_strategy(self, strategy: StrategySpec) -> None:
        self._validate_worker_launch_for_host(strategy)
        if strategy.worker_host == "opencode":
            return
        if strategy.driver != "builtin":
            raise ValueError(
                f"{strategy.worker_host} worker_host only supports builtin "
                "default/agent_guided and random strategies"
            )
        if not portable_strategy_mode(strategy.name):
            raise ValueError(
                f"{strategy.worker_host} worker_host does not support strategy "
                f"{strategy.name}; use default/agent_guided or random"
            )
        self._validate_worker_budget_for_host(
            worker_host=strategy.worker_host,
            worker_agent_type=strategy.worker_agent_type,
            worker_budget=strategy.worker_budget,
        )

    def _validate_worker_launch_for_host(self, strategy: StrategySpec) -> None:
        if strategy.worker_launch is None:
            return
        adapter = get_agent_host_adapter(strategy.worker_host)
        requested = strategy.worker_launch.model_dump(mode="json", exclude_none=True)
        capability_by_field = {
            "model": adapter.capabilities.supports_model_override,
            "reasoning_effort": adapter.capabilities.supports_reasoning_effort,
            "service_tier": adapter.capabilities.supports_service_tier,
        }
        unsupported = sorted(
            field for field in requested if not capability_by_field[field]
        )
        if unsupported:
            raise ValueError(
                f"{strategy.worker_host} worker_host does not support worker_launch "
                f"fields: {', '.join(unsupported)}"
            )

    def _validate_worker_budget_for_host(
        self,
        *,
        worker_host: str,
        worker_agent_type: str | None,
        worker_budget: WorkerBudget | None,
    ) -> None:
        if worker_host == "pi-rpc" and (
            worker_budget is None or worker_budget.max_runtime_seconds is None
        ):
            raise ValueError(
                "pi-rpc worker_budget requires max_runtime_seconds so the "
                "Pi RPC runner can enforce a process deadline"
            )
        if worker_budget is None:
            return
        if worker_host == "codex" and worker_budget.max_runtime_seconds is None:
            raise ValueError(
                "codex worker_budget requires max_runtime_seconds so the "
                "parent agent can enforce a watchdog deadline"
            )
        if worker_host == "claude-code" and worker_budget.max_turns is None:
            raise ValueError(
                "claude-code worker_budget requires max_turns so the "
                "subagent definition can enforce a turn budget"
            )
        if worker_host != "claude-code":
            return

        turns = worker_budget.max_turns
        configured_agent = worker_agent_type
        if configured_agent in CLAUDE_CODE_KNOWN_AGENT_TURN_BUDGETS:
            expected = CLAUDE_CODE_KNOWN_AGENT_TURN_BUDGETS[configured_agent]
            if turns != expected:
                raise ValueError(
                    "known claude-code worker_agent_type "
                    f"{configured_agent!r} has maxTurns {expected}, "
                    f"not requested worker_budget.max_turns {turns}"
                )
        elif configured_agent is None and turns not in CLAUDE_CODE_AGENT_TYPE_BY_TURN_BUDGET:
            supported = sorted(CLAUDE_CODE_AGENT_TYPE_BY_TURN_BUDGET)
            raise ValueError(
                "claude-code worker_budget.max_turns without an explicit "
                "custom worker_agent_type must be one of "
                f"{supported}"
            )

    def _worker_budget_dict(self, strategy: StrategySpec) -> dict[str, Any] | None:
        if strategy.worker_budget is None:
            return None
        return strategy.worker_budget.model_dump(mode="json")

    def _worker_launch_dict(self, strategy: StrategySpec) -> dict[str, Any] | None:
        if strategy.worker_launch is None:
            return None
        return strategy.worker_launch.model_dump(mode="json", exclude_none=True)

    def _worker_policy(self, strategy: StrategySpec) -> dict[str, Any]:
        adapter = get_agent_host_adapter(strategy.worker_host)
        worker_agent_type = strategy.worker_agent_type
        worker_budget = self._worker_budget_dict(strategy)
        worker_launch = self._worker_launch_dict(strategy)
        if (
            strategy.worker_host == "claude-code"
            and worker_agent_type is None
            and strategy.worker_budget is not None
            and strategy.worker_budget.max_turns is not None
        ):
            worker_agent_type = CLAUDE_CODE_AGENT_TYPE_BY_TURN_BUDGET[
                strategy.worker_budget.max_turns
            ]
        return {
            "mode": "agent-session-pool",
            "configured_mode": strategy.worker_mode,
            "host": strategy.worker_host,
            "worker_agent_type": worker_agent_type,
            "subagent_type": worker_agent_type,
            "worker_budget": worker_budget,
            "worker_launch": worker_launch,
            "supports_bind_handle": adapter.capabilities.supports_bind_handle,
            "supports_same_worker_continue": adapter.capabilities.supports_same_worker_continue,
            "supports_trace_export": adapter.capabilities.supports_trace_export,
            "uses_background_workers": adapter.capabilities.uses_background_workers,
            "continuation": adapter.capabilities.continuation,
            "supports_soft_closeout": adapter.capabilities.supports_soft_closeout,
            "supports_model_override": adapter.capabilities.supports_model_override,
            "supports_reasoning_effort": adapter.capabilities.supports_reasoning_effort,
            "supports_service_tier": adapter.capabilities.supports_service_tier,
            "supports_usage_metadata": adapter.capabilities.supports_usage_metadata,
            "supports_process_kill": adapter.capabilities.supports_process_kill,
            "pool": adapter.capabilities.pool.as_dict(),
            "directive_rule": (
                "Worker directives should describe the candidate idea and deliverable, not score "
                "targets or baseline scores. Workers must treat any score target in a directive as "
                "main-agent context only and must not run local scoring to satisfy it."
            ),
            "requires_agent_session": True,
            "direct_edit_allowed": False,
            "reason": (
                f"worker_mode=agent-session-pool requires the main agent to launch "
                f"{strategy.worker_host} workers through the published host-pool "
                "contract using launch payloads from search_start_agent_session."
            ),
        }

    def _normalize_worker_policy(
        self,
        strategy: StrategySpec,
        worker_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base_policy = self._worker_policy(strategy)
        policy = {**base_policy, **(worker_policy or {})}
        selected = (
            policy.get("subagent_type")
            or policy.get("worker_agent_type")
            or strategy.worker_agent_type
            or self._default_worker_agent_type(strategy.worker_host)
        )
        policy["worker_agent_type"] = selected
        policy["subagent_type"] = selected
        policy.setdefault("mode", "agent-session-pool")
        policy.setdefault("configured_mode", strategy.worker_mode)
        policy.setdefault("requires_agent_session", True)
        policy.setdefault("direct_edit_allowed", False)
        return policy

    def _default_worker_agent_type(self, host: str) -> str:
        if host == "codex":
            return "search_candidate_agent"
        if host == "claude-code":
            return "search-candidate-agent"
        if host == "pi-rpc":
            return "search-candidate-worker"
        return "SearchCandidateAgent"

    def _candidate_worker_agent_type(
        self,
        frozen: FrozenSpec,
        candidate_record: CandidateRecord,
    ) -> str:
        worker_policy = candidate_record.task.strategy_metadata.get("worker_policy", {})
        selected = (
            worker_policy.get("subagent_type")
            or worker_policy.get("worker_agent_type")
            or frozen.spec.strategy.worker_agent_type
            or self._default_worker_agent_type(frozen.spec.strategy.worker_host)
        )
        return str(selected)

    def _candidate_worker_budget(
        self,
        frozen: FrozenSpec,
        candidate_record: CandidateRecord,
    ) -> dict[str, Any] | None:
        worker_policy = candidate_record.task.strategy_metadata.get("worker_policy", {})
        budget = worker_policy.get("worker_budget")
        if budget is not None:
            return dict(budget)
        return self._worker_budget_dict(frozen.spec.strategy)

    def _candidate_worker_launch(
        self,
        frozen: FrozenSpec,
        candidate_record: CandidateRecord,
    ) -> dict[str, Any] | None:
        worker_policy = candidate_record.task.strategy_metadata.get("worker_policy", {})
        launch = worker_policy.get("worker_launch")
        if launch is not None:
            return dict(launch)
        return self._worker_launch_dict(frozen.spec.strategy)

    def _normalize_worker_budget_override(
        self,
        *,
        worker_host: str,
        worker_agent_type: str | None,
        worker_budget: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if worker_budget is None:
            return None
        parsed = WorkerBudget.model_validate(worker_budget)
        self._validate_worker_budget_for_host(
            worker_host=worker_host,
            worker_agent_type=worker_agent_type,
            worker_budget=parsed,
        )
        return parsed.model_dump(mode="json")

    def _resolve_worker_budget_for_dispatch(
        self,
        *,
        frozen: FrozenSpec,
        candidate_record: CandidateRecord,
        worker_agent_type: str | None,
        worker_budget_override: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        worker_budget = (
            worker_budget_override
            if worker_budget_override is not None
            else self._candidate_worker_budget(frozen, candidate_record)
        )
        return self._normalize_worker_budget_override(
            worker_host=frozen.spec.strategy.worker_host,
            worker_agent_type=worker_agent_type,
            worker_budget=worker_budget,
        )

    def _build_launch_payload(
        self,
        frozen: FrozenSpec,
        candidate_id: str,
        agent_session_id: str,
        directive: dict[str, Any],
        candidate_record: CandidateRecord,
        worker_agent_type_override: str | None = None,
        worker_budget_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        worker_agent_type = (
            worker_agent_type_override
            or self._candidate_worker_agent_type(frozen, candidate_record)
        )
        proposal = candidate_record.task.proposal
        if proposal is not None and proposal.intent:
            short_intent = proposal.intent
        elif directive.get("goal"):
            short_intent = str(directive["goal"])
        else:
            short_intent = candidate_record.task.hypothesis

        idea_lines: list[str] = []
        if proposal is not None and proposal.intent:
            idea_lines.append(f"candidate_intent: {proposal.intent}")
            if proposal.hypothesis:
                idea_lines.append(f"candidate_hypothesis: {proposal.hypothesis}")
            if proposal.expected_tradeoff:
                idea_lines.append(f"expected_tradeoff: {proposal.expected_tradeoff}")
            if proposal.instructions:
                idea_lines.append(
                    "candidate_instructions: " + " | ".join(proposal.instructions)
                )
        else:
            idea_lines.append(f"candidate_hypothesis: {candidate_record.task.hypothesis}")
        if directive:
            idea_lines.extend(
                f"main_directive.{key}: {value}" for key, value in directive.items()
            )
        one_paragraph_idea = "; ".join(idea_lines)

        adapter = get_agent_host_adapter(frozen.spec.strategy.worker_host)
        return adapter.build_launch_payload(
            worker_agent_type=worker_agent_type,
            candidate_id=candidate_id,
            agent_session_id=agent_session_id,
            short_intent=short_intent,
            one_paragraph_idea=one_paragraph_idea,
            worker_budget=(
                worker_budget_override
                if worker_budget_override is not None
                else self._candidate_worker_budget(frozen, candidate_record)
            ),
            worker_launch=self._candidate_worker_launch(frozen, candidate_record),
            root=str(self.root_dir),
            cwd=str(candidate_record.task.workspace),
            worker_prompt=self._worker_prompt_for_host(frozen.spec.strategy.worker_host),
        )

    def _build_continue_launch_payload(
        self,
        frozen: FrozenSpec,
        session: AgentSessionRecord,
        directive: dict[str, Any],
        candidate_record: CandidateRecord,
        worker_budget_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        worker_agent_type = self._candidate_worker_agent_type(frozen, candidate_record)
        if directive.get("goal"):
            short_intent = str(directive["goal"])
        else:
            short_intent = "continue same candidate"

        if directive:
            directive_text = "; ".join(
                f"{key}: {value}" for key, value in directive.items()
            )
        else:
            directive_text = (
                "continue improving the same candidate from its current workspace state"
            )

        adapter = get_agent_host_adapter(session.host)
        return adapter.build_continue_payload(
            worker_agent_type=worker_agent_type,
            candidate_id=session.candidate_id,
            agent_session_id=session.agent_session_id,
            external_id=session.host_handle.external_id or session.opencode_session_id,
            task_name=session.host_handle.task_name,
            short_intent=short_intent,
            one_paragraph_idea=directive_text,
            root=str(self.root_dir),
            cwd=str(candidate_record.task.workspace),
            worker_prompt=self._worker_prompt_for_host(session.host),
            worker_budget=(
                worker_budget_override
                if worker_budget_override is not None
                else self._candidate_worker_budget(frozen, candidate_record)
            ),
        )

    def _worker_prompt_for_host(self, host: str) -> str | None:
        if host != "pi-rpc":
            return None
        prompt_path = Path(__file__).resolve().parents[2] / ".pi" / "prompts" / "search-candidate-worker.md"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return (
            "First call search_get_agent_context. Work in the candidate workspace only. "
            "Before final response call search_run_verifier. Use runtime history; "
            "do not rely on transcript. If the verifier reports "
            "VerifierWorkspaceSideEffect or candidate_action=stop_and_report, "
            "report the infrastructure blocker and return without retrying."
        )

    def _next_plan_id(self, run: RunRecord) -> str:
        plan_id = f"plan_{run.next_plan_index:03d}"
        run.next_plan_index += 1
        return plan_id

    def _normalize_main_directive(
        self,
        main_directive: dict[str, Any] | str | None,
    ) -> dict[str, Any]:
        if main_directive is None:
            return {}
        if isinstance(main_directive, str):
            return {"goal": main_directive}
        if isinstance(main_directive, dict):
            return main_directive
        raise TypeError("main_directive must be a dict, string, or null")

    def _plan_independent(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        requested_k: int,
        planned_k: int,
        remaining: int,
    ) -> SearchPlan:
        work_orders = []
        for slot in range(1, planned_k + 1):
            hypothesis_index = run.candidates_total + slot - 1
            planned_candidate_id = f"c{run.next_candidate_index + slot - 1:03d}"
            hypothesis = (
                frozen.spec.root_hypotheses[hypothesis_index]
                if hypothesis_index < len(frozen.spec.root_hypotheses)
                else f"Independent candidate {planned_candidate_id}"
            )
            work_orders.append(
                CandidateWorkOrder(
                    slot=slot,
                    intent=hypothesis,
                    hypothesis=hypothesis,
                    metadata={"strategy": "independent_branches"},
                )
            )

        return SearchPlan(
            run_id=run.run_id,
            plan_id=self._next_plan_id(run),
            strategy=frozen.spec.strategy,
            requested_k=requested_k,
            planned_k=planned_k,
            remaining_budget=remaining,
            requires_agent_proposals=False,
            official_history=self._history_view(run, frozen, frozen.spec.strategy.history_policy),
            derivation_policy={
                "base_workspace_source": "source",
                "may_derive_from_source": True,
            },
            work_orders=work_orders,
            strategy_trace={
                "selection_rule": "independent source branches",
                "reason": "Each candidate starts from the frozen source workspace.",
            },
            created_at=utc_timestamp(),
        )

    def _plan_agent_guided(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        requested_k: int,
        planned_k: int,
        remaining: int,
    ) -> SearchPlan:
        history = self._history_view(run, frozen, frozen.spec.strategy.history_policy)
        candidate_ids = [candidate["candidate_id"] for candidate in history.get("candidates", [])]
        notes = [
            "The main agent may remember more chat history, but this is the official runtime view for the next batch.",
            "Submitted proposals must cite at least one official candidate when candidate references are available.",
        ]
        return SearchPlan(
            run_id=run.run_id,
            plan_id=self._next_plan_id(run),
            strategy=frozen.spec.strategy,
            requested_k=requested_k,
            planned_k=planned_k,
            remaining_budget=remaining,
            requires_agent_proposals=True,
            official_history=history,
            derivation_policy={
                "base_workspace_source": "proposal.base_candidate_id or source",
                "must_reference_one_of": candidate_ids,
            },
            proposal_contract=ProposalContract(
                count=planned_k,
                must_reference_one_of=candidate_ids,
                notes=notes,
            ),
            strategy_trace={
                "selection_rule": f"agent-guided history policy: {frozen.spec.strategy.history_policy.scope}",
                "reason": "The runtime provides the official history view; the main agent proposes the next candidates.",
            },
            created_at=utc_timestamp(),
        )

    def _plan_evolve(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        requested_k: int,
        planned_k: int,
        remaining: int,
    ) -> SearchPlan:
        records = self._load_candidate_records(run.run_id)
        scored = self._scored_records(records, frozen.spec)

        if not scored:
            plan = self._plan_independent(run, frozen, requested_k, planned_k, remaining)
            plan.strategy_trace = {
                "selection_rule": "evolve bootstrap",
                "reason": "No verified parent exists yet, so the first generation starts from source.",
            }
            return plan

        parent = self._best_record(scored, frozen.spec)
        top_records = self._top_records(scored, frozen.spec, frozen.spec.strategy.history_policy.top_n)
        inspirations = [record for record in top_records if record.candidate_id != parent.candidate_id]
        inspiration_ids = [record.candidate_id for record in inspirations]

        work_orders = []
        for slot in range(1, planned_k + 1):
            work_orders.append(
                CandidateWorkOrder(
                    slot=slot,
                    base_candidate_id=parent.candidate_id,
                    parent_candidate_ids=[parent.candidate_id],
                    inspiration_candidate_ids=inspiration_ids,
                    intent=(
                        f"Mutate `{parent.candidate_id}` using the selected inspirations; "
                        "preserve the parent's strongest metrics and explore one concrete tradeoff."
                    ),
                    hypothesis=f"Evolve mutation from {parent.candidate_id} slot {slot}",
                    must_derive_from=[parent.candidate_id],
                    metadata={
                        "strategy": "evolve",
                        "parent_score": parent.score_report.aggregate_score if parent.score_report else None,
                    },
                )
            )

        visible_ids = [parent.candidate_id, *inspiration_ids]
        return SearchPlan(
            run_id=run.run_id,
            plan_id=self._next_plan_id(run),
            strategy=frozen.spec.strategy,
            requested_k=requested_k,
            planned_k=planned_k,
            remaining_budget=remaining,
            requires_agent_proposals=False,
            official_history=self._history_view(
                run,
                frozen,
                frozen.spec.strategy.history_policy,
                forced_candidate_ids=visible_ids,
            ),
            derivation_policy={
                "base_workspace_source": f"candidate:{parent.candidate_id}",
                "must_derive_from": [parent.candidate_id],
                "may_reference": inspiration_ids,
            },
            work_orders=work_orders,
            strategy_trace={
                "selection_rule": "best verified parent plus top inspirations",
                "parent_candidate_id": parent.candidate_id,
                "inspiration_candidate_ids": inspiration_ids,
                "reason": "Builtin evolve-mode approximates OpenEvolve-style fixed parent selection.",
            },
            created_at=utc_timestamp(),
        )

    def _plan_openevolve(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        requested_k: int,
        planned_k: int,
        remaining: int,
    ) -> SearchPlan:
        records = self._load_candidate_records(run.run_id)
        scored = self._records_by_created(self._scored_records(records, frozen.spec))
        strategy = frozen.spec.strategy
        config = strategy.config

        if not scored:
            plan = self._plan_independent(run, frozen, requested_k, planned_k, remaining)
            plan.strategy_trace = {
                "selection_rule": "openevolve bootstrap",
                "sampling_mode": "bootstrap",
                "reason": (
                    "No verified parent exists yet, so OpenEvolve starts by creating "
                    "source-derived programs before database sampling."
                ),
            }
            return plan

        rng = self._openevolve_rng(config, run.next_plan_index)
        archive = self._openevolve_archive(
            scored,
            frozen.spec,
            int(config.get("archive_size", 100)),
        )
        parent, sampling_mode, rand_val = self._openevolve_sample_parent(
            scored,
            archive,
            rng,
            exploration_ratio=float(config.get("exploration_ratio", 0.2)),
            exploitation_ratio=float(config.get("exploitation_ratio", 0.7)),
        )
        num_inspirations = int(config.get("num_inspirations", 5))
        inspirations = self._openevolve_sample_inspirations(
            parent,
            scored,
            archive,
            frozen.spec,
            rng,
            num_inspirations,
        )
        inspiration_ids = [record.candidate_id for record in inspirations]

        work_orders: list[CandidateWorkOrder] = []
        for slot in range(1, planned_k + 1):
            work_orders.append(
                CandidateWorkOrder(
                    slot=slot,
                    base_candidate_id=parent.candidate_id,
                    parent_candidate_ids=[parent.candidate_id],
                    inspiration_candidate_ids=inspiration_ids,
                    intent=(
                        f"OpenEvolve mutation from `{parent.candidate_id}` using sampled "
                        "inspirations; make a small diff-like change and keep verifier feedback."
                    ),
                    hypothesis=f"OpenEvolve mutation from {parent.candidate_id} slot {slot}",
                    instructions=[
                        (
                            f"OpenEvolve sampled parent `{parent.candidate_id}` via "
                            f"{sampling_mode}; mutate this workspace instead of restarting."
                        ),
                        (
                            "Use inspirations as design hints only; preserve the parent's "
                            "working behavior before exploring one concrete change."
                        ),
                        (
                            "Prefer a compact diff-style mutation, then verify through "
                            "goal-plus_search_run_verifier."
                        ),
                    ],
                    must_derive_from=[parent.candidate_id],
                    metadata={
                        "strategy": "openevolve",
                        "sampling_mode": sampling_mode,
                        "rand_val": rand_val,
                        "parent_score": (
                            parent.score_report.aggregate_score if parent.score_report else None
                        ),
                        "archive_candidate_ids": [record.candidate_id for record in archive],
                    },
                )
            )

        visible_ids = [parent.candidate_id, *inspiration_ids]
        return SearchPlan(
            run_id=run.run_id,
            plan_id=self._next_plan_id(run),
            strategy=strategy,
            requested_k=requested_k,
            planned_k=planned_k,
            remaining_budget=remaining,
            requires_agent_proposals=False,
            official_history=self._history_view(
                run,
                frozen,
                strategy.history_policy,
                forced_candidate_ids=visible_ids,
            ),
            derivation_policy={
                "base_workspace_source": f"candidate:{parent.candidate_id}",
                "must_derive_from": [parent.candidate_id],
                "may_reference": inspiration_ids,
            },
            work_orders=work_orders,
            strategy_trace={
                "selection_rule": "openevolve sampled parent plus inspirations",
                "sampling_mode": sampling_mode,
                "rand_val": rand_val,
                "parent_candidate_id": parent.candidate_id,
                "archive_candidate_ids": [record.candidate_id for record in archive],
                "inspiration_candidate_ids": inspiration_ids,
                "reason": (
                    "OpenEvolve-style base planner samples a parent from exploration, "
                    "archive exploitation, or random fallback, then passes sampled "
                    "inspirations to the worker as mutation context."
                ),
            },
            created_at=utc_timestamp(),
        )

    def _openevolve_rng(self, config: dict[str, Any], plan_index: int) -> random.Random:
        seed = config.get("seed", config.get("random_seed"))
        if seed is None:
            return random.Random()
        return random.Random(int(seed) + plan_index)

    def _openevolve_archive(
        self,
        scored: list[CandidateRecord],
        spec: SearchSpec,
        archive_size: int,
    ) -> list[CandidateRecord]:
        if archive_size <= 0:
            return []
        return self._top_records(scored, spec, min(archive_size, len(scored)))

    def _openevolve_sample_parent(
        self,
        scored: list[CandidateRecord],
        archive: list[CandidateRecord],
        rng: random.Random,
        *,
        exploration_ratio: float,
        exploitation_ratio: float,
    ) -> tuple[CandidateRecord, str, float]:
        rand_val = rng.random()
        if rand_val < exploration_ratio:
            return rng.choice(scored), "exploration", rand_val
        if rand_val < exploration_ratio + exploitation_ratio and archive:
            return rng.choice(archive), "exploitation", rand_val
        return rng.choice(scored), "random", rand_val

    def _openevolve_sample_inspirations(
        self,
        parent: CandidateRecord,
        scored: list[CandidateRecord],
        archive: list[CandidateRecord],
        spec: SearchSpec,
        rng: random.Random,
        count: int,
    ) -> list[CandidateRecord]:
        if count <= 0:
            return []

        inspirations: list[CandidateRecord] = []
        seen = {parent.candidate_id}

        best = self._best_record(scored, spec)
        if best.candidate_id not in seen:
            inspirations.append(best)
            seen.add(best.candidate_id)

        for record in archive:
            if len(inspirations) >= count:
                return inspirations
            if record.candidate_id not in seen:
                inspirations.append(record)
                seen.add(record.candidate_id)

        remaining = [record for record in scored if record.candidate_id not in seen]
        while remaining and len(inspirations) < count:
            record = rng.choice(remaining)
            remaining = [item for item in remaining if item.candidate_id != record.candidate_id]
            inspirations.append(record)
            seen.add(record.candidate_id)

        return inspirations

    def _plan_mcts(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        requested_k: int,
        planned_k: int,
        remaining: int,
    ) -> SearchPlan:
        records = self._load_candidate_records(run.run_id)
        scored = self._scored_records(records, frozen.spec)
        if not scored:
            plan = self._plan_independent(run, frozen, requested_k, planned_k, remaining)
            plan.strategy_trace = {
                "selection_rule": "mcts bootstrap",
                "reason": "No verified node exists yet, so the first expansion starts from source.",
            }
            return plan

        frontier = self._best_record(scored, frozen.spec)
        work_orders = [
            CandidateWorkOrder(
                slot=slot,
                base_candidate_id=frontier.candidate_id,
                parent_candidate_ids=[frontier.candidate_id],
                intent=(
                    f"Expand frontier candidate `{frontier.candidate_id}` with a distinct action."
                ),
                hypothesis=f"MCTS-style expansion from {frontier.candidate_id} slot {slot}",
                must_derive_from=[frontier.candidate_id],
                metadata={"strategy": "mcts", "frontier_node": frontier.candidate_id},
            )
            for slot in range(1, planned_k + 1)
        ]
        return SearchPlan(
            run_id=run.run_id,
            plan_id=self._next_plan_id(run),
            strategy=frozen.spec.strategy,
            requested_k=requested_k,
            planned_k=planned_k,
            remaining_budget=remaining,
            requires_agent_proposals=False,
            official_history=self._history_view(
                run,
                frozen,
                frozen.spec.strategy.history_policy,
                forced_candidate_ids=[frontier.candidate_id],
            ),
            derivation_policy={
                "base_workspace_source": f"candidate:{frontier.candidate_id}",
                "must_expand_node": frontier.candidate_id,
                "must_derive_from": [frontier.candidate_id],
            },
            work_orders=work_orders,
            strategy_trace={
                "selection_rule": "best-score frontier placeholder",
                "frontier_node": frontier.candidate_id,
                "reason": "Builtin mcts-mode exposes the same plan contract; a full UCB tree policy can replace this planner.",
            },
            created_at=utc_timestamp(),
        )

    def _plan_random(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        requested_k: int,
        planned_k: int,
        remaining: int,
    ) -> SearchPlan:
        records = self._load_candidate_records(run.run_id)
        scored = self._scored_records(records, frozen.spec)

        if not scored:
            plan = self._plan_independent(run, frozen, requested_k, planned_k, remaining)
            plan.strategy_trace = {
                "selection_rule": "random bootstrap",
                "reason": "No verified parent exists yet, so the first generation starts from source.",
            }
            return plan

        seed = frozen.spec.strategy.config.get("seed")
        rng = random.Random(seed) if seed is not None else random
        parent = rng.choice(scored)

        work_orders = []
        for slot in range(1, planned_k + 1):
            work_orders.append(
                CandidateWorkOrder(
                    slot=slot,
                    base_candidate_id=parent.candidate_id,
                    parent_candidate_ids=[parent.candidate_id],
                    inspiration_candidate_ids=[],
                    intent=(
                        f"Mutate randomly chosen parent `{parent.candidate_id}`; "
                        "explore a different direction than the parent."
                    ),
                    hypothesis=f"Random mutation from {parent.candidate_id} slot {slot}",
                    must_derive_from=[parent.candidate_id],
                    metadata={
                        "strategy": "random",
                        "parent_score": parent.score_report.aggregate_score if parent.score_report else None,
                    },
                )
            )

        return SearchPlan(
            run_id=run.run_id,
            plan_id=self._next_plan_id(run),
            strategy=frozen.spec.strategy,
            requested_k=requested_k,
            planned_k=planned_k,
            remaining_budget=remaining,
            requires_agent_proposals=False,
            official_history=self._history_view(
                run,
                frozen,
                frozen.spec.strategy.history_policy,
                forced_candidate_ids=[parent.candidate_id],
            ),
            derivation_policy={
                "base_workspace_source": f"candidate:{parent.candidate_id}",
                "must_derive_from": [parent.candidate_id],
            },
            work_orders=work_orders,
            strategy_trace={
                "selection_rule": "random verified parent",
                "parent_candidate_id": parent.candidate_id,
                "seed": seed,
                "reason": "Builtin random-mode picks one verified parent at random for the next generation.",
            },
            created_at=utc_timestamp(),
        )

    def _plan_custom_strategy(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        requested_k: int,
        planned_k: int,
        remaining: int,
    ) -> SearchPlan:
        strategy = frozen.spec.strategy
        if strategy.driver == "python":
            if not strategy.ref:
                raise ValueError("python strategy requires strategy.ref")
            return self._plan_python_strategy(run, frozen, requested_k, planned_k, remaining)

        if strategy.driver == "external_mcp":
            history_policy = strategy.history_policy
            history = self._history_view(run, frozen, history_policy)
            candidate_ids = [candidate["candidate_id"] for candidate in history.get("candidates", [])]
            return SearchPlan(
                run_id=run.run_id,
                plan_id=self._next_plan_id(run),
                strategy=strategy,
                requested_k=requested_k,
                planned_k=planned_k,
                remaining_budget=remaining,
                requires_agent_proposals=True,
                official_history=history,
                derivation_policy={
                    "base_workspace_source": "external strategy proposal",
                    "must_reference_one_of": candidate_ids,
                },
                proposal_contract=ProposalContract(
                    count=planned_k,
                    must_reference_one_of=candidate_ids,
                    notes=[
                        "external_mcp strategy is represented through the standard proposal contract in this runtime",
                        "call the external strategy separately, then pass its proposals to search_start_batch",
                    ],
                ),
                strategy_trace={
                    "selection_rule": "external strategy proposal contract",
                    "external_ref": strategy.ref,
                    "reason": "External MCP strategy integration is modeled as proposals submitted back to this runtime.",
                },
                created_at=utc_timestamp(),
            )

        raise ValueError(f"unsupported strategy driver: {strategy.driver}")

    def _plan_python_strategy(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        requested_k: int,
        planned_k: int,
        remaining: int,
    ) -> SearchPlan:
        strategy = frozen.spec.strategy
        module_name, sep, attr_name = (strategy.ref or "").partition(":")
        if not sep:
            raise ValueError("python strategy ref must use 'module:object'")
        module = importlib.import_module(module_name)
        strategy_factory = getattr(module, attr_name)
        strategy_object = strategy_factory(strategy.config)
        if not hasattr(strategy_object, "plan_next"):
            raise TypeError("python strategy object must define plan_next(payload)")

        full_history = self.list_history(
            run.run_id,
            top_n=max(1, len(self._load_candidate_records(run.run_id))),
            sort_by="created",
        )
        payload = {
            "run": run.model_dump(mode="json"),
            "spec": frozen.spec.model_dump(mode="json"),
            "history": full_history,
            "requested_k": requested_k,
            "planned_k": planned_k,
            "remaining_budget": remaining,
        }
        planned = strategy_object.plan_next(payload)
        if not isinstance(planned, dict):
            raise TypeError("python strategy plan_next must return a dict")

        plan_data = {
            **planned,
            "run_id": run.run_id,
            "plan_id": self._next_plan_id(run),
            "strategy": strategy.model_dump(mode="json"),
            "requested_k": requested_k,
            "planned_k": planned_k,
            "remaining_budget": remaining,
            "created_at": utc_timestamp(),
        }
        return SearchPlan.model_validate(plan_data)

    def _proposal_from_work_order(self, work_order: CandidateWorkOrder) -> CandidateProposal:
        return CandidateProposal(
            parent_candidate_ids=work_order.parent_candidate_ids,
            base_candidate_id=work_order.base_candidate_id,
            hypothesis=work_order.hypothesis,
            intent=work_order.intent,
            instructions=work_order.instructions,
            history_refs=work_order.inspiration_candidate_ids,
            metadata={
                **work_order.metadata,
                "slot": work_order.slot,
                "must_derive_from": work_order.must_derive_from,
            },
        )

    def _validate_agent_proposals(
        self,
        plan: SearchPlan,
        proposals: list[CandidateProposal],
    ) -> None:
        contract = plan.proposal_contract
        if contract is None:
            return
        if len(proposals) > contract.count:
            raise ValueError("too many proposals for this plan")
        required_refs = set(contract.must_reference_one_of)
        if not required_refs:
            return
        for proposal in proposals:
            refs = set(proposal.parent_candidate_ids)
            refs.update(proposal.history_refs)
            if proposal.base_candidate_id:
                refs.add(proposal.base_candidate_id)
            if not refs.intersection(required_refs):
                raise ValueError(
                    "proposal must reference at least one official candidate from the plan"
                )

    def _create_candidate_task(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        candidate_id: str,
        plan: SearchPlan,
        proposal: CandidateProposal,
        slot: int,
    ) -> CandidateTask:
        workspace = self._run_dir(run.run_id) / "workspace" / candidate_id
        base_candidate_id = proposal.base_candidate_id
        parent_candidate_ids = list(proposal.parent_candidate_ids)
        if base_candidate_id is None and parent_candidate_ids:
            base_candidate_id = parent_candidate_ids[0]
        if base_candidate_id and base_candidate_id not in parent_candidate_ids:
            parent_candidate_ids.insert(0, base_candidate_id)

        base_workspace: Path | None = None
        base_revision: str | None = None
        if base_candidate_id:
            base_record = self._load_candidate_record(run.run_id, base_candidate_id)
            base_workspace = base_record.task.workspace
            if frozen.spec.workspace.backend == "git_worktree":
                best_iteration = self._best_git_iteration_record(
                    base_record, frozen.spec.metric_direction
                )
                if best_iteration is None:
                    raise RuntimeError(
                        f"git_worktree parent {base_candidate_id} has no clean "
                        "verifier-backed Git iteration"
                    )
                base_revision = best_iteration.git_head

        materialization = materialize_candidate_workspace(
            backend=frozen.spec.workspace.backend,
            run_dir=self._run_dir(run.run_id),
            source=Path(run.source_path),
            workspace=workspace,
            run_id=run.run_id,
            candidate_id=candidate_id,
            base_workspace=base_workspace,
            base_revision=base_revision,
        )

        instructions = [
            "Work only inside this candidate workspace.",
            "Use this workspace's .tmp/ directory for notes and scratch drafts.",
            "Do not use /tmp, home directories, or paths outside the candidate workspace for candidate work.",
            "Modify only files listed in allowed_files; never touch denied_files or frozen verifier artifacts.",
            "Do not delete, move, or clean files; destructive commands such as rm, mv, rmdir, unlink, trash, and find -delete are forbidden.",
            "A local git repository has already been initialized with the copied baseline; use git status, git diff, git add, git commit, git reset, git restore, and git checkout only inside this workspace.",
            "All scoring must go through goal-plus_search_run_verifier; do not run the process_verifiers command directly via bash, and do not write your own scorer.",
            "Pass context.agent_session_id to search_run_verifier so the runtime can record iteration provenance.",
            "Each run_verifier call records an iteration. Work within the configured host budget. Complete and verify a candidate early, stop starting new optimization iterations before the limit, and leave enough time to return a concise summary.",
            "search_run_verifier automatically commits changed candidate artifact files before running the verifier; use git status, git diff, and git log to inspect iteration provenance.",
            "Maintain an iteration log at workspace/.tmp/results.tsv with header: commit \\t <metric_name> \\t status \\t hypothesis (use the literal context.metric_name value as the column-2 header). After each verifier call, use the returned/runtime-recorded git_head as the commit column.",
        ]
        if plan.worker_policy.get("subagent_type"):
            instructions.append(
                f"Use subagent_type={plan.worker_policy['subagent_type']!r} for the managed agent session."
            )
        instructions.extend(proposal.instructions)

        parent_id = parent_candidate_ids[0] if parent_candidate_ids else None
        hypothesis = proposal.hypothesis or proposal.intent or f"Candidate {candidate_id}"
        return CandidateTask(
            run_id=run.run_id,
            candidate_id=candidate_id,
            parent_id=parent_id,
            parent_candidate_ids=parent_candidate_ids,
            base_candidate_id=base_candidate_id,
            plan_id=plan.plan_id,
            hypothesis=hypothesis,
            workspace=workspace,
            workspace_backend=materialization.backend,
            workspace_branch=materialization.branch,
            workspace_base_revision=materialization.base_revision,
            allowed_files=frozen.spec.edit_surface.allow,
            denied_files=frozen.spec.edit_surface.deny,
            instructions=instructions,
            expected_artifacts=["patch", "notes", "logs"],
            stop_conditions={},
            proposal=proposal,
            strategy_metadata={
                "strategy": plan.strategy.name,
                "strategy_driver": plan.strategy.driver,
                "worker_mode": plan.worker_policy.get("mode"),
                "worker_policy": plan.worker_policy,
                "plan_id": plan.plan_id,
                "slot": slot,
                "workspace_backend": materialization.backend,
                "workspace_branch": materialization.branch,
                "workspace_base_revision": materialization.base_revision,
            },
        )

    def _history_view(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        policy: HistoryPolicy,
        forced_candidate_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        records = self._load_candidate_records(run.run_id)
        selected_records: list[CandidateRecord]
        scope = policy.scope

        if forced_candidate_ids is not None:
            by_id = {record.candidate_id: record for record in records}
            selected_records = [
                by_id[candidate_id]
                for candidate_id in forced_candidate_ids
                if candidate_id in by_id
            ]
            scope = "selected_parent_and_inspirations"
        elif policy.scope == "all":
            selected_records = self._records_by_created(records)
        elif policy.scope == "last_batch":
            selected_records = self._last_batch_records(records)
        else:
            selected_records = self._top_records(records, frozen.spec, policy.top_n)

        if policy.scope != "all" and forced_candidate_ids is None:
            selected_records = selected_records[: policy.top_n]

        return {
            "policy": scope,
            "top_n": policy.top_n,
            "include": policy.include,
            "visible_candidate_ids": [record.candidate_id for record in selected_records],
            "candidates": [
                self._history_candidate_payload(record, frozen.spec)
                for record in selected_records
            ],
            "description": (
                "Official runtime-selected history view for the current strategy plan."
            ),
        }

    def _record_ranking_score(
        self,
        record: CandidateRecord,
        spec: SearchSpec,
    ) -> float | None:
        best_iteration = self._best_iteration_record(record, spec.metric_direction)
        if best_iteration is not None:
            return best_iteration.score
        if (
            record.score_report
            and record.score_report.process_passed
            and record.score_report.aggregate_score is not None
        ):
            return record.score_report.aggregate_score
        return None

    def _scored_records(
        self,
        records: list[CandidateRecord],
        spec: SearchSpec,
    ) -> list[CandidateRecord]:
        return [
            record
            for record in records
            if self._record_ranking_score(record, spec) is not None
        ]

    def _best_record(self, records: list[CandidateRecord], spec: SearchSpec) -> CandidateRecord:
        reverse = spec.metric_direction == "maximize"
        return sorted(
            records,
            key=lambda record: self._record_ranking_score(record, spec),
            reverse=reverse,
        )[0]  # type: ignore[arg-type,return-value]

    def _top_records(
        self,
        records: list[CandidateRecord],
        spec: SearchSpec,
        top_n: int,
    ) -> list[CandidateRecord]:
        scored = self._scored_records(records, spec)
        if not scored:
            return self._records_by_created(records)[:top_n]
        reverse = spec.metric_direction == "maximize"
        return sorted(
            scored,
            key=lambda record: self._record_ranking_score(record, spec),
            reverse=reverse,
        )[:top_n]  # type: ignore[arg-type,return-value]

    def _records_by_created(self, records: list[CandidateRecord]) -> list[CandidateRecord]:
        def created_index(record: CandidateRecord) -> int:
            try:
                return int(record.candidate_id.removeprefix("c"))
            except ValueError:
                return 0

        return sorted(records, key=created_index)

    def _last_batch_records(self, records: list[CandidateRecord]) -> list[CandidateRecord]:
        plan_ids = [record.task.plan_id for record in records if record.task.plan_id]
        if not plan_ids:
            return self._records_by_created(records)
        last_plan_id = sorted(plan_ids)[-1]
        return self._records_by_created(
            [record for record in records if record.task.plan_id == last_plan_id]
        )

    def _markdown_cell(self, value: str) -> str:
        return value.replace("\n", " ").replace("|", "\\|")

    def _precheck_candidate(
        self,
        frozen: FrozenSpec,
        record: CandidateRecord,
    ) -> ScoreReport | None:
        results: list[VerifierResult] = []

        if record.touched_denied_files or record.changed_outside_allowed:
            outside_allowed = [
                path
                for path in record.detected_changed_files
                if not path_matches(path, frozen.spec.edit_surface.allow)
            ]
            frozen_paths = set(frozen.verifier_hashes)
            verifier_workspace_side_effects = bool(outside_allowed) and all(
                path.startswith(".goal-plus-verifiers/")
                and path not in frozen_paths
                for path in outside_allowed
            )
            failure_class = (
                "VerifierWorkspaceSideEffect"
                if verifier_workspace_side_effects
                else "EditSurfaceViolation"
            )
            results.append(
                VerifierResult(
                    name="edit_surface_check",
                    role=VerifierRole.ANTI_CHEAT_GATE,
                    passed=False,
                    score=0.0,
                    metrics={
                        "detected_changed_files": record.detected_changed_files,
                        "touched_denied_files": record.touched_denied_files,
                        "changed_outside_allowed": record.changed_outside_allowed,
                        "infrastructure_failure": verifier_workspace_side_effects,
                        "candidate_action": (
                            "stop_and_report"
                            if verifier_workspace_side_effects
                            else "repair_candidate_edit_surface"
                        ),
                    },
                    failure_class=failure_class,
                )
            )

        hash_failures = self._frozen_hash_failures(frozen, record.task.workspace)
        if hash_failures:
            results.append(
                VerifierResult(
                    name="frozen_hash_check",
                    role=VerifierRole.ANTI_CHEAT_GATE,
                    passed=False,
                    score=0.0,
                    metrics={"hash_failures": hash_failures},
                    failure_class="FrozenVerifierModified",
                )
            )

        if not results:
            return None

        return ScoreReport(
            run_id=record.task.run_id,
            candidate_id=record.candidate_id,
            parent_id=record.task.parent_id,
            validity_passed=False,
            process_passed=False,
            promotion_passed=None,
            aggregate_score=0.0,
            verifier_results=results,
            touched_denied_files=record.touched_denied_files,
            changed_outside_allowed=record.changed_outside_allowed,
            hardcoding_suspected=any(
                result.failure_class
                in {"EditSurfaceViolation", "FrozenVerifierModified"}
                for result in results
            ),
        )

    def _run_commands(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        record: CandidateRecord,
        commands: list[VerifierCommand],
        scope: str,
    ) -> ScoreReport:
        verifier_phase: Literal["candidate", "promotion"] = (
            "candidate" if scope == "process" else "promotion"
        )
        results: list[VerifierResult] = []
        for command in commands:
            result = self._run_command(
                run,
                frozen,
                record,
                command,
                verifier_phase,
            )
            results.append(result)
            if result.failure_class == "VerifierWorkspaceSideEffect":
                break
        hard_failed = any(
            not result.passed
            and result.role
            in {
                VerifierRole.VALIDITY_GATE,
                VerifierRole.PROCESS_GATE,
                VerifierRole.PROMOTION_GATE,
                VerifierRole.ANTI_CHEAT_GATE,
            }
            for result in results
        )
        process_passed = not hard_failed and all(
            result.passed or result.role == VerifierRole.DIAGNOSTIC_SIGNAL for result in results
        )
        score = self._aggregate_score(frozen.spec.metric_name, results)
        if not process_passed:
            score = 0.0

        return ScoreReport(
            run_id=run.run_id,
            candidate_id=record.candidate_id,
            parent_id=record.task.parent_id,
            validity_passed=process_passed,
            process_passed=process_passed,
            promotion_passed=process_passed if scope == "promotion" else None,
            aggregate_score=score,
            verifier_results=results,
            touched_denied_files=record.touched_denied_files,
            changed_outside_allowed=record.changed_outside_allowed,
            hardcoding_suspected=False,
        )

    def _run_command(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        record: CandidateRecord,
        command: VerifierCommand,
        verifier_phase: Literal["candidate", "promotion"],
    ) -> VerifierResult:
        if command.command[0] == "goal-plus-internal":
            return self._run_internal_command(frozen, record, command)

        log_scope = "process" if verifier_phase == "candidate" else "promotion"
        logs_dir = (
            self._run_dir(run.run_id)
            / "candidates"
            / record.candidate_id
            / "logs"
            / log_scope
        )
        logs_dir.mkdir(parents=True, exist_ok=True)
        command_log_name = safe_verifier_name(command.name)
        log_path = logs_dir / f"{command_log_name}.log"
        diagnostics_dir = (
            logs_dir
            / "diagnostics"
            / f"{command_log_name}-{uuid.uuid4().hex[:12]}"
        )
        diagnostics_dir.mkdir(parents=True, exist_ok=False)
        cwd = (record.task.workspace / command.cwd).resolve()
        workspace_before = self._hash_verifier_workspace(record.task.workspace)
        git_head_before = self._git_head(record.task.workspace)
        start = time.perf_counter()
        try:
            with verifier_resource_lock(command.resource_lock):
                with tempfile.TemporaryDirectory(
                    prefix="goal-plus-verifier-command-"
                ) as verifier_tmp:
                    completed = self._execute_verifier_process(
                        command.command,
                        cwd=cwd,
                        env=self._verifier_environment(
                            cwd,
                            Path(verifier_tmp),
                            phase=verifier_phase,
                            diagnostics_dir=diagnostics_dir,
                            resource=command.resource_lock,
                        ),
                        text=True,
                        capture_output=True,
                        timeout=command.timeout_seconds,
                        check=False,
                    )
            elapsed = time.perf_counter() - start
            metrics = self._parse_metrics(completed.stdout)
            metrics.setdefault("returncode", completed.returncode)
            metrics.setdefault("elapsed_seconds", elapsed)
            metrics.update(self._verifier_diagnostics(diagnostics_dir))
            side_effects = self._hash_changes(
                workspace_before,
                self._hash_verifier_workspace(record.task.workspace),
            )
            if side_effects:
                cleanup_failures = self._restore_verifier_workspace(
                    record.task.workspace,
                    workspace_before,
                    side_effects,
                    git_head_before,
                )
                metrics.update(
                    {
                        "verifier_workspace_side_effects": side_effects,
                        "cleanup_failures": cleanup_failures,
                        "infrastructure_failure": True,
                        "candidate_action": "stop_and_report",
                    }
                )
                log_path.write_text(
                    _bounded_log(
                        "\n".join(
                            [
                                f"$ {' '.join(command.command)}",
                                f"cwd: {cwd}",
                                f"returncode: {completed.returncode}",
                                f"verifier_workspace_side_effects: {side_effects}",
                                f"cleanup_failures: {cleanup_failures}",
                                "",
                                "## stdout",
                                completed.stdout,
                                "## stderr",
                                completed.stderr,
                            ]
                        )
                    ),
                    encoding="utf-8",
                )
                return VerifierResult(
                    name=command.name,
                    role=command.role,
                    passed=False,
                    score=0.0,
                    metrics=metrics,
                    log_path=log_path,
                    failure_class="VerifierWorkspaceSideEffect",
                )
            score = self._score_from_metrics(frozen.spec.metric_name, metrics)
            has_verifier_error = self._has_verifier_error(metrics)
            missing_numeric_metric = (
                completed.returncode == 0
                and not has_verifier_error
                and command.role == VerifierRole.RANKING_SIGNAL
                and score is None
            )
            if missing_numeric_metric:
                metrics["expected_metric_name"] = frozen.spec.metric_name
                metrics["stdout_tail"] = completed.stdout.strip()[-2000:]
            passed = (
                completed.returncode == 0
                and not has_verifier_error
                and not missing_numeric_metric
            )
            log_path.write_text(
                _bounded_log(
                    "\n".join(
                        [
                            f"$ {' '.join(command.command)}",
                            f"cwd: {cwd}",
                            f"returncode: {completed.returncode}",
                            "",
                            "## stdout",
                            completed.stdout,
                            "## stderr",
                            completed.stderr,
                        ]
                    )
                ),
                encoding="utf-8",
            )
            return VerifierResult(
                name=command.name,
                role=command.role,
                passed=passed,
                score=score,
                metrics=metrics,
                log_path=log_path,
                failure_class=(
                    None
                    if passed
                    else (
                        "MissingNumericMetric"
                        if missing_numeric_metric
                        else "VerifierCommandFailed"
                    )
                ),
            )
        except subprocess.TimeoutExpired as exc:
            side_effects = self._hash_changes(
                workspace_before,
                self._hash_verifier_workspace(record.task.workspace),
            )
            cleanup_failures = self._restore_verifier_workspace(
                record.task.workspace,
                workspace_before,
                side_effects,
                git_head_before,
            ) if side_effects else []
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            log_path.write_text(
                _bounded_log(
                    "\n".join(
                        [
                            f"$ {' '.join(command.command)}",
                            f"cwd: {cwd}",
                            f"timeout_seconds: {command.timeout_seconds}",
                            "",
                            "## stdout",
                            stdout,
                            "## stderr",
                            stderr,
                        ]
                    )
                ),
                encoding="utf-8",
            )
            metrics: dict[str, Any] = {
                "timeout_seconds": command.timeout_seconds,
                "verifier_workspace_side_effects": side_effects,
                "cleanup_failures": cleanup_failures,
                "infrastructure_failure": bool(side_effects),
                "candidate_action": (
                    "stop_and_report" if side_effects else "inspect_timeout"
                ),
            }
            metrics.update(self._verifier_diagnostics(diagnostics_dir))
            return VerifierResult(
                name=command.name,
                role=command.role,
                passed=False,
                score=0.0,
                metrics=metrics,
                log_path=log_path,
                failure_class=(
                    "VerifierWorkspaceSideEffect" if side_effects else "Timeout"
                ),
            )
        except OSError as exc:
            log_path.write_text(_bounded_log(str(exc)), encoding="utf-8")
            metrics: dict[str, Any] = {"error": str(exc)}
            metrics.update(self._verifier_diagnostics(diagnostics_dir))
            return VerifierResult(
                name=command.name,
                role=command.role,
                passed=False,
                score=0.0,
                metrics=metrics,
                log_path=log_path,
                failure_class="VerifierStartFailed",
            )

    def _verifier_diagnostics(self, diagnostics_dir: Path) -> dict[str, Any]:
        files = sorted(
            path.relative_to(diagnostics_dir).as_posix()
            for path in diagnostics_dir.rglob("*")
            if path.is_file()
        )
        if not files:
            shutil.rmtree(diagnostics_dir, ignore_errors=True)
            return {}
        return {
            "diagnostics_dir": str(diagnostics_dir),
            "diagnostic_files": files,
        }

    def _restore_verifier_workspace(
        self,
        workspace: Path,
        before: dict[str, str],
        side_effects: list[str],
        git_head_before: str | None,
    ) -> list[str]:
        cleanup_failures: list[str] = []
        if git_head_before is not None:
            try:
                self._git_output(
                    workspace,
                    ["git", "reset", "--hard", git_head_before],
                )
            except (FileNotFoundError, subprocess.CalledProcessError):
                cleanup_failures.extend(
                    path for path in side_effects if path in before
                )
        else:
            cleanup_failures.extend(path for path in side_effects if path in before)

        for rel_path in side_effects:
            if rel_path in before:
                continue
            target = workspace / rel_path
            try:
                if target.is_file() or target.is_symlink():
                    target.unlink()
                parent = target.parent
                while parent != workspace:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
            except OSError:
                cleanup_failures.append(rel_path)

        remaining = self._hash_changes(
            before,
            self._hash_verifier_workspace(workspace),
        )
        cleanup_failures.extend(
            path for path in remaining if path not in cleanup_failures
        )
        return cleanup_failures

    def _run_internal_command(
        self,
        frozen: FrozenSpec,
        record: CandidateRecord,
        command: VerifierCommand,
    ) -> VerifierResult:
        if len(command.command) < 2 or command.command[1] != "check-frozen-hashes":
            return VerifierResult(
                name=command.name,
                role=command.role,
                passed=False,
                score=0.0,
                metrics={"error": "unknown internal command"},
                failure_class="UnknownInternalCommand",
            )
        failures = self._frozen_hash_failures(frozen, record.task.workspace)
        return VerifierResult(
            name=command.name,
            role=command.role,
            passed=not failures,
            score=1.0 if not failures else 0.0,
            metrics={"hash_failures": failures},
            failure_class=None if not failures else "FrozenVerifierModified",
        )

    def _parse_metrics(self, stdout: str) -> dict[str, Any]:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _has_verifier_error(self, metrics: dict[str, Any]) -> bool:
        """Treat a non-null top-level error value as verifier failure."""
        return metrics.get("error") is not None

    def _score_from_metrics(self, metric_name: str, metrics: dict[str, Any]) -> float | None:
        for key in (metric_name, "combined_score", "score", "overall_score"):
            value = metrics.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                score = float(value)
                if math.isfinite(score):
                    return score
        return None

    def _aggregate_score(self, metric_name: str, results: list[VerifierResult]) -> float | None:
        for result in results:
            if result.score is not None and result.role != VerifierRole.ANTI_CHEAT_GATE:
                return result.score
            score = self._score_from_metrics(metric_name, result.metrics)
            if score is not None:
                return score
        return None

    def _update_best_seen(self, run: RunRecord, spec: SearchSpec, report: ScoreReport) -> None:
        if not report.process_passed or report.aggregate_score is None:
            return
        if run.best_score is None:
            run.best_score = report.aggregate_score
            run.best_candidate_id = report.candidate_id
            return
        is_better = (
            report.aggregate_score > run.best_score
            if spec.metric_direction == "maximize"
            else report.aggregate_score < run.best_score
        )
        if is_better:
            run.best_score = report.aggregate_score
            run.best_candidate_id = report.candidate_id

    def _best_current_artifact_iteration(
        self,
        run: RunRecord,
        record: CandidateRecord,
        metric_direction: Literal["maximize", "minimize"],
    ) -> IterationRecord | None:
        current_changed = self._detect_changed_files(
            Path(run.source_path), record.task.workspace
        )
        current_artifact_hash = self._artifact_hash(
            record.task.workspace, current_changed
        )
        selectable = [
            iteration
            for iteration in record.iterations
            if iteration.process_passed is True
            and iteration.score is not None
            and iteration.artifact_hash == current_artifact_hash
            and not iteration.touched_denied_files
            and not iteration.changed_outside_allowed
        ]
        if not selectable:
            return None
        reverse = metric_direction == "maximize"
        return sorted(
            selectable, key=lambda iteration: iteration.score, reverse=reverse
        )[0]

    def _best_iteration_record(
        self,
        record: CandidateRecord,
        metric_direction: Literal["maximize", "minimize"],
    ) -> IterationRecord | None:
        scored = [
            iteration
            for iteration in record.iterations
            if iteration.process_passed is True
            and iteration.score is not None
            and not iteration.touched_denied_files
            and not iteration.changed_outside_allowed
        ]
        if not scored:
            return None
        reverse = metric_direction == "maximize"
        return sorted(
            scored, key=lambda iteration: iteration.score, reverse=reverse
        )[0]

    def _best_git_iteration_record(
        self,
        record: CandidateRecord,
        metric_direction: Literal["maximize", "minimize"],
    ) -> IterationRecord | None:
        scored = [
            iteration
            for iteration in record.iterations
            if iteration.process_passed is True
            and iteration.score is not None
            and iteration.git_head is not None
            and iteration.git_artifact_clean is True
            and not iteration.touched_denied_files
            and not iteration.changed_outside_allowed
        ]
        if not scored:
            return None
        reverse = metric_direction == "maximize"
        return sorted(
            scored, key=lambda iteration: iteration.score, reverse=reverse
        )[0]

    def _selection_options(
        self,
        run: RunRecord,
        records: list[CandidateRecord],
        metric_direction: Literal["maximize", "minimize"],
    ) -> list[tuple[float, CandidateRecord, int | None, str | None]]:
        options: list[tuple[float, CandidateRecord, int | None, str | None]] = []
        for record in records:
            current_changed = self._detect_changed_files(
                Path(run.source_path), record.task.workspace
            )
            current_artifact_hash = self._artifact_hash(
                record.task.workspace, current_changed
            )
            report_is_represented = False
            for iteration in record.iterations:
                if (
                    iteration.process_passed is not True
                    or iteration.score is None
                    or iteration.touched_denied_files
                    or iteration.changed_outside_allowed
                ):
                    continue
                if iteration.git_head and iteration.git_artifact_clean is True:
                    options.append(
                        (
                            iteration.score,
                            record,
                            iteration.iteration,
                            iteration.git_head,
                        )
                    )
                elif iteration.artifact_hash == current_artifact_hash:
                    options.append((iteration.score, record, iteration.iteration, None))
                if (
                    record.score_report
                    and iteration.artifact_hash == current_artifact_hash
                    and iteration.process_passed == record.score_report.process_passed
                    and iteration.score == record.score_report.aggregate_score
                ):
                    report_is_represented = True

            if (
                record.score_report
                and record.score_report.process_passed
                and record.score_report.aggregate_score is not None
                and not record.touched_denied_files
                and not record.changed_outside_allowed
                and not report_is_represented
            ):
                options.append(
                    (record.score_report.aggregate_score, record, None, None)
                )
        return options

    def _candidate_research_summary(
        self,
        run_id: str,
        candidate_id: str,
    ) -> dict[str, Any]:
        """Return the latest bounded worker-authored research handoff.

        This is deliberately a compact cross-round summary, not a transcript
        or a generalized experience-memory layer. Older handoff keys remain
        readable so existing runs still contribute useful evidence.
        """

        def items(value: Any) -> list[Any]:
            if value is None or value == "":
                return []
            if isinstance(value, list):
                return value[:5]
            return [value]

        for session in reversed(self._load_agent_sessions(run_id)):
            if session.candidate_id != candidate_id:
                continue
            metadata = session.host_handle.metadata or {}
            progress = metadata.get("progress_handoff")
            if not isinstance(progress, dict):
                continue
            model_handoff = progress.get("model_handoff")
            if not isinstance(model_handoff, dict):
                continue
            summary = model_handoff.get("summary")
            if not isinstance(summary, str):
                summary = progress.get("summary")
            return {
                "summary": summary if isinstance(summary, str) else "",
                "key_results": items(
                    model_handoff.get("key_results", model_handoff.get("what_was_tried"))
                ),
                "pitfalls": items(model_handoff.get("pitfalls")),
                "blockers": items(model_handoff.get("blockers")),
                "next_steps": items(
                    model_handoff.get("next_steps", model_handoff.get("next_action"))
                ),
                "source_agent_session_id": session.agent_session_id,
            }
        return {
            "summary": "",
            "key_results": [],
            "pitfalls": [],
            "blockers": [],
            "next_steps": [],
            "source_agent_session_id": None,
        }

    def _history_candidate_payload(
        self,
        record: CandidateRecord,
        spec: SearchSpec,
    ) -> dict[str, Any]:
        score_report = record.score_report
        best_iteration = self._best_iteration_record(record, spec.metric_direction)
        evidence_score = (
            best_iteration.score
            if best_iteration is not None
            else score_report.aggregate_score if score_report else None
        )
        evidence_passed = (
            True
            if best_iteration is not None
            else score_report.process_passed if score_report else None
        )
        metrics: dict[str, Any] = {}
        verifier_summaries: list[dict[str, Any]] = []
        failure_classes: list[str] = []
        log_paths: list[str] = []
        latest_verifier_summaries: list[dict[str, Any]] = []
        latest_failure_classes: list[str] = []
        latest_log_paths: list[str] = []

        score_report_is_evidence = bool(
            score_report
            and score_report.process_passed
            and score_report.aggregate_score == evidence_score
        )
        if best_iteration is not None:
            for verifier_metrics in best_iteration.metrics.values():
                if isinstance(verifier_metrics, dict):
                    metrics.update(
                        {
                            key: value
                            for key, value in verifier_metrics.items()
                            if key not in metrics
                        }
                    )
        if score_report:
            for result in score_report.verifier_results:
                if result.failure_class:
                    latest_failure_classes.append(result.failure_class)
                if result.log_path:
                    latest_log_paths.append(str(result.log_path))
                latest_verifier_summaries.append(
                    {
                        "name": result.name,
                        "role": result.role,
                        "passed": result.passed,
                        "score": result.score,
                        "failure_class": result.failure_class,
                        "log_path": str(result.log_path) if result.log_path else None,
                    }
                )
            if score_report_is_evidence:
                if not metrics:
                    for result in score_report.verifier_results:
                        if result.metrics:
                            metrics = result.metrics
                            break
                failure_classes = list(latest_failure_classes)
                log_paths = list(latest_log_paths)
                verifier_summaries = list(latest_verifier_summaries)

        key_metrics = {
            key: value
            for key, value in metrics.items()
            if key
            not in {
                "returncode",
                "elapsed_seconds",
            }
            and isinstance(value, int | float | bool | str)
        }

        agent_sessions = self._agent_session_payloads_for_candidate(
            record.task.run_id,
            record.candidate_id,
        )
        research_summary = self._candidate_research_summary(
            record.task.run_id,
            record.candidate_id,
        )
        risk_notes = [
            (
                "Condition: {condition}; failed approach: {failed_approach}; "
                "reason: {reason}; recommendation: {recommendation}".format(
                    condition=pitfall.get("condition", "the recorded condition"),
                    failed_approach=pitfall.get("failed_approach", "the approach"),
                    reason=pitfall.get("reason", "the recorded reason"),
                    recommendation=pitfall.get("recommendation", "avoid repeating it"),
                )
                if isinstance(pitfall, dict)
                else str(pitfall)
            )
            for pitfall in research_summary["pitfalls"]
        ]

        return {
            "candidate_id": record.candidate_id,
            "parent_id": record.task.parent_id,
            "parent_candidate_ids": record.task.parent_candidate_ids,
            "base_candidate_id": record.task.base_candidate_id,
            "plan_id": record.task.plan_id,
            "status": record.status,
            "hypothesis": record.task.hypothesis,
            "intent": record.task.proposal.intent if record.task.proposal else record.task.hypothesis,
            "expected_tradeoff": (
                record.task.proposal.expected_tradeoff if record.task.proposal else ""
            ),
            "history_refs": record.task.proposal.history_refs if record.task.proposal else [],
            "strategy_metadata": record.task.strategy_metadata,
            "workspace": str(record.task.workspace),
            "agent_sessions": agent_sessions,
            "summary": research_summary["summary"],
            "key_results": research_summary["key_results"],
            "next_ideas": research_summary["next_steps"],
            "risk_notes": risk_notes,
            "blockers": research_summary["blockers"],
            "research_summary": research_summary,
            "artifact_status": None,
            "changed_files": record.detected_changed_files,
            "touched_denied_files": record.touched_denied_files,
            "changed_outside_allowed": record.changed_outside_allowed,
            "process_passed": evidence_passed,
            "score": evidence_score,
            "metric_name": spec.metric_name,
            "evidence_source": "best_iteration" if best_iteration else "latest_score_report",
            "best_iteration": best_iteration.iteration if best_iteration else None,
            "best_git_head": best_iteration.git_head if best_iteration else None,
            "latest_process_passed": score_report.process_passed if score_report else None,
            "latest_score": score_report.aggregate_score if score_report else None,
            "latest_failure_classes": latest_failure_classes,
            "latest_verifiers": latest_verifier_summaries,
            "latest_log_paths": latest_log_paths,
            "key_metrics": key_metrics,
            "failure_classes": failure_classes,
            "verifiers": verifier_summaries,
            "log_paths": log_paths,
        }

    def _agent_session_payloads_for_candidate(
        self,
        run_id: str,
        candidate_id: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "agent_session_id": session.agent_session_id,
                "candidate_id": session.candidate_id,
                "host": session.host,
                "host_handle": session.host_handle.model_dump(mode="json"),
                "host_handle_display": self._display_host_handle(session),
                "opencode_session_id": session.opencode_session_id,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "directive": session.directive,
                "verifier_runs": session.counters.get("verifier_runs", 0),
            }
            for session in self._load_agent_sessions(run_id)
            if session.candidate_id == candidate_id
        ]

    def _frozen_hash_failures(self, frozen: FrozenSpec, workspace: Path) -> dict[str, dict[str, str | None]]:
        failures: dict[str, dict[str, str | None]] = {}
        for rel_path, expected_hash in frozen.verifier_hashes.items():
            path = workspace / rel_path
            actual_hash = sha256_file(path) if path.exists() and path.is_file() else None
            if actual_hash != expected_hash:
                failures[rel_path] = {"expected": expected_hash, "actual": actual_hash}
        return failures

    def _detect_changed_files(self, source: Path, workspace: Path) -> list[str]:
        source_hashes = self._hash_tree(source, source_view=True)
        workspace_hashes = self._hash_tree(workspace)
        changed: list[str] = []
        for rel_path in sorted(set(source_hashes) | set(workspace_hashes)):
            if source_hashes.get(rel_path) != workspace_hashes.get(rel_path):
                changed.append(rel_path)
        return changed

    def _artifact_hash(self, workspace: Path, changed_files: list[str]) -> str:
        payload: dict[str, str | None] = {}
        for rel_path in sorted(changed_files):
            path = workspace / rel_path
            payload[rel_path] = sha256_file(path) if path.is_file() else None
        return sha256_text(canonical_json(payload))

    def _git_head(self, workspace: Path) -> str | None:
        try:
            value = self._git_output(
                workspace, ["git", "rev-parse", "--verify", "HEAD"]
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None
        return value.strip() or None

    def _git_status(self, workspace: Path) -> list[str]:
        try:
            value = self._git_output(
                workspace,
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return []
        return [line for line in value.splitlines() if line.strip()]

    def _git_artifact_clean(
        self,
        workspace: Path,
        changed_files: list[str],
        git_head: str | None,
    ) -> bool:
        if not git_head:
            return False
        if not changed_files:
            return True
        try:
            value = self._git_output(
                workspace,
                [
                    "git",
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                    "--",
                    *changed_files,
                ],
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
        return not value.strip()

    def _git_output(self, workspace: Path, command: list[str]) -> str:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate()
        if process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode,
                command,
                output=stdout,
                stderr=stderr,
            )
        return stdout

    def _git_returncode(self, workspace: Path, command: list[str]) -> int:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        process.communicate()
        return process.returncode

    def _commit_workspace_iteration(
        self,
        workspace: Path,
        changed_files: list[str],
        message: str,
    ) -> str | None:
        if not changed_files:
            return self._git_head(workspace)
        try:
            self._git_output(workspace, ["git", "add", "--", *changed_files])
            staged_returncode = self._git_returncode(
                workspace,
                ["git", "diff", "--cached", "--quiet", "--", *changed_files],
            )
            if staged_returncode == 0:
                return self._git_head(workspace)
            if staged_returncode != 1:
                return None
            self._git_output(
                workspace,
                [
                    "git",
                    "-c",
                    "user.name=goal-plus",
                    "-c",
                    "user.email=goal-plus@example.invalid",
                    "commit",
                    "-q",
                    "--no-verify",
                    "-m",
                    message,
                ],
            )
            return self._git_head(workspace)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

    def _checkout_git_revision(self, workspace: Path, revision: str) -> None:
        try:
            subprocess.check_call(
                ["git", "checkout", "-q", "--detach", revision],
                cwd=workspace,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                f"failed to checkout candidate revision {revision}"
            ) from exc

    def _hash_tree(
        self,
        root: Path,
        *,
        source_view: bool = False,
    ) -> dict[str, str]:
        hashes: dict[str, str] = {}
        paths = list_source_files(root) if source_view else list_files(root)
        for path in paths:
            rel_path = path.relative_to(root).as_posix()
            hashes[rel_path] = sha256_file(path)
        return hashes

    def _write_patch(
        self,
        source: Path,
        workspace: Path,
        selected_revision: str,
        changed_files: list[str],
        patch_path: Path,
    ) -> None:
        if not changed_files:
            patch_path.write_text("", encoding="utf-8")
            return

        with tempfile.TemporaryDirectory(prefix="goal-plus-patch-") as temporary:
            repository = Path(temporary) / "repository"
            copy_source_tree(source, repository)
            baseline = initialize_workspace_git_baseline(repository)
            if baseline is None:
                raise RuntimeError("cannot initialize temporary promotion repository")
            for rel_path in changed_files:
                staged = repository / rel_path
                if staged.exists() or staged.is_symlink():
                    if staged.is_dir() and not staged.is_symlink():
                        shutil.rmtree(staged)
                    else:
                        staged.unlink()
                entry = self._git_tree_entry(
                    workspace,
                    selected_revision,
                    rel_path,
                )
                if entry is None:
                    continue
                mode, object_type, object_id = entry
                if object_type == "tree":
                    staged.mkdir(parents=True, exist_ok=True)
                    continue
                if object_type != "blob":
                    raise RuntimeError(
                        "selected promotion revision contains unsupported Git "
                        f"object type {object_type!r} at {rel_path}"
                    )
                content = self._git_blob(workspace, object_id)
                staged.parent.mkdir(parents=True, exist_ok=True)
                if mode == "120000":
                    staged.symlink_to(os.fsdecode(content))
                else:
                    staged.write_bytes(content)
                    staged.chmod(int(mode, 8) & 0o777)
            self._git_output(
                repository,
                ["git", "add", "-A", "--", *changed_files],
            )
            patch = self._git_diff(
                repository,
                baseline,
                changed_files,
                cached=True,
            )
        patch_path.write_text(patch, encoding="utf-8")

    def _git_tree_entry(
        self,
        repository: Path,
        revision: str,
        rel_path: str,
    ) -> tuple[str, str, str] | None:
        command = [
            "git",
            "--no-replace-objects",
            "ls-tree",
            "-z",
            revision,
            "--",
            f":(literal){rel_path}",
        ]
        try:
            process = subprocess.run(
                command,
                cwd=repository,
                check=True,
                capture_output=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", b"")
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            raise RuntimeError(
                "failed to read immutable promotion revision: "
                f"{str(detail).strip()}"
            ) from exc
        if not process.stdout:
            return None
        entries = [entry for entry in process.stdout.split(b"\0") if entry]
        if len(entries) != 1:
            raise RuntimeError(
                f"selected promotion revision has ambiguous path {rel_path!r}"
            )
        metadata, _path = entries[0].split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split()
        return mode, object_type, object_id

    def _git_blob(self, repository: Path, object_id: str) -> bytes:
        command = ["git", "--no-replace-objects", "cat-file", "blob", object_id]
        try:
            return subprocess.run(
                command,
                cwd=repository,
                check=True,
                capture_output=True,
            ).stdout
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", b"")
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"failed to read immutable promotion blob: {str(detail).strip()}"
            ) from exc

    def _git_diff(
        self,
        repository: Path,
        baseline: str,
        changed_files: list[str],
        *,
        cached: bool,
    ) -> str:
        command = [
            "git",
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
        ]
        if cached:
            command.append("--cached")
        command.extend([baseline, "--", *changed_files])
        try:
            return subprocess.run(
                command,
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise RuntimeError(
                f"failed to generate promotion patch: {detail.strip()}"
            ) from exc

    def _spec_dir(self, frozen_spec_id: str) -> Path:
        return self.specs_dir / frozen_spec_id

    def _run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    @contextmanager
    def _run_transaction(self, run_id: str):
        with exclusive_file_lock(self._run_dir(run_id) / "run.lock"):
            yield

    def _candidate_dir(self, run_id: str, candidate_id: str) -> Path:
        return self._run_dir(run_id) / "candidates" / candidate_id

    def _plan_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "plans"

    def _agent_session_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "agent_sessions"

    @staticmethod
    def _make_agent_session_id(run_id: str, index: int) -> str:
        run_suffix = run_id.removeprefix("run_")
        return f"agent_{run_suffix}_{index:03d}"

    def _load_agent_session_by_id(
        self,
        agent_session_id: str,
        run_id: str | None = None,
    ) -> AgentSessionRecord:
        if run_id is not None:
            path = self._agent_session_dir(run_id) / f"{agent_session_id}.json"
            if path.exists():
                return AgentSessionRecord.model_validate(load_json(path))
            raise FileNotFoundError(
                f"agent session not found: {agent_session_id} in run {run_id}"
            )

        matches = sorted(self.runs_dir.glob(f"*/agent_sessions/{agent_session_id}.json"))
        if len(matches) == 1:
            return AgentSessionRecord.model_validate(load_json(matches[0]))
        if len(matches) > 1:
            match_runs = ", ".join(path.parents[1].name for path in matches)
            raise RuntimeError(
                f"ambiguous agent_session_id {agent_session_id}; matched runs: {match_runs}. "
                "Use a globally unique agent_session_id from search_start_agent_session."
            )
        raise FileNotFoundError(f"agent session not found: {agent_session_id}")

    def _write_agent_session(self, session: AgentSessionRecord) -> None:
        write_json(
            self._agent_session_dir(session.run_id) / f"{session.agent_session_id}.json",
            session.model_dump(mode="json"),
        )

    def _load_agent_sessions(self, run_id: str) -> list[AgentSessionRecord]:
        session_dir = self._agent_session_dir(run_id)
        if not session_dir.exists():
            return []
        return [
            AgentSessionRecord.model_validate(load_json(path))
            for path in sorted(session_dir.glob("agent_*.json"))
        ]

    def _load_frozen_spec(self, frozen_spec_id: str) -> FrozenSpec:
        data = load_json(self._spec_dir(frozen_spec_id) / "frozen_spec.json")
        spec_data = data.get("spec")
        if isinstance(spec_data, dict) and "workspace" not in spec_data:
            # Frozen specs created before workspace backends were persisted used
            # an independent copy for every candidate. Preserve that behavior
            # when resuming legacy runs even though new specs default to a
            # shared-object Git worktree layout.
            spec_data["workspace"] = {"backend": "copy"}
        return FrozenSpec.model_validate(data)

    def _load_run(self, run_id: str) -> RunRecord:
        return RunRecord.model_validate(load_json(self._run_dir(run_id) / "run.json"))

    def _write_run(self, run: RunRecord) -> None:
        write_json(self._run_dir(run.run_id) / "run.json", run.model_dump(mode="json"))

    def _load_plan(self, run_id: str, plan_id: str) -> SearchPlan:
        return SearchPlan.model_validate(load_json(self._plan_dir(run_id) / f"{plan_id}.json"))

    def _write_plan(self, plan: SearchPlan) -> None:
        write_json(
            self._plan_dir(plan.run_id) / f"{plan.plan_id}.json",
            plan.model_dump(mode="json"),
        )

    def _load_plans(self, run_id: str) -> list[SearchPlan]:
        plan_dir = self._plan_dir(run_id)
        if not plan_dir.exists():
            return []
        return [
            SearchPlan.model_validate(load_json(path))
            for path in sorted(plan_dir.glob("plan_*.json"))
        ]

    def _load_candidate_record(self, run_id: str, candidate_id: str) -> CandidateRecord:
        return CandidateRecord.model_validate(
            load_json(self._candidate_dir(run_id, candidate_id) / "candidate.json")
        )

    def _write_candidate_record(self, run_id: str, record: CandidateRecord) -> None:
        candidate_dir = self._candidate_dir(run_id, record.candidate_id)
        write_json(candidate_dir / "candidate.json", record.model_dump(mode="json"))
        write_json(candidate_dir / "task.json", record.task.model_dump(mode="json"))

    def _load_candidate_records(self, run_id: str) -> list[CandidateRecord]:
        candidates_dir = self._run_dir(run_id) / "candidates"
        if not candidates_dir.exists():
            return []
        records = []
        for path in sorted(candidates_dir.glob("*/candidate.json")):
            records.append(CandidateRecord.model_validate(load_json(path)))
        return records
