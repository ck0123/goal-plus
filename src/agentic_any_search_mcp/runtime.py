from __future__ import annotations

from contextlib import contextmanager
import difflib
import calendar
import hashlib
import importlib
import json
import os
import random
import shutil
import subprocess
import time
import uuid
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None  # type: ignore[assignment]

from agentic_any_search_mcp.agent_hosts import (
    UnsupportedHostCapability,
    get_agent_host_adapter,
    portable_strategy_mode,
)
from agentic_any_search_mcp.models import (
    AgentHostHandle,
    AgentSessionRecord,
    CandidateRecord,
    CandidateProposal,
    CandidateTask,
    CandidateWorkOrder,
    FrozenSpec,
    HistoryPolicy,
    ProposalContract,
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
)


IGNORED_NAMES = {".git", ".search", ".tmp", ".pytest_cache", "__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
CLAUDE_CODE_KNOWN_AGENT_TURN_BUDGETS = {
    "any-search-agent-flash": 4,
    "any-search-agent": 8,
    "any-search-agent-deep": 16,
}
CLAUDE_CODE_AGENT_TYPE_BY_TURN_BUDGET = {
    turns: agent_type
    for agent_type, turns in CLAUDE_CODE_KNOWN_AGENT_TURN_BUDGETS.items()
}


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


def should_ignore(path: Path) -> bool:
    if any(part in IGNORED_NAMES for part in path.parts):
        return True
    return path.suffix in IGNORED_SUFFIXES


def list_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and not should_ignore(path.relative_to(root)):
            files.append(path)
    return sorted(files)


def copy_source_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if source.is_file():
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination / source.name)
        return

    def ignore(_dir: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in IGNORED_NAMES or Path(name).suffix in IGNORED_SUFFIXES:
                ignored.add(name)
        return ignored

    shutil.copytree(source, destination, ignore=ignore)


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


def relative_artifact_path(source_root: Path, artifact_path: Path) -> str:
    artifact = artifact_path.resolve()
    try:
        return artifact.relative_to(source_root.resolve()).as_posix()
    except ValueError:
        return artifact.name


class FileSearchRuntime:
    def __init__(
        self,
        root_dir: Path | str = ".search",
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.specs_dir = self.root_dir / "specs"
        self.runs_dir = self.root_dir / "runs"
        self.specs_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def freeze_spec(self, spec: SearchSpec, verifier_artifacts: list[Path]) -> FrozenSpec:
        source_root = Path(spec.source_path).resolve()
        verifier_hashes: dict[str, str] = {}

        for artifact in verifier_artifacts:
            artifact_path = Path(artifact).resolve()
            if not artifact_path.exists() or not artifact_path.is_file():
                raise FileNotFoundError(f"verifier artifact not found: {artifact_path}")
            rel_path = relative_artifact_path(source_root, artifact_path)
            verifier_hashes[rel_path] = sha256_file(artifact_path)

        spec_payload = spec.model_dump(mode="json")
        spec_hash = sha256_text(canonical_json({"spec": spec_payload, "verifiers": verifier_hashes}))
        frozen_spec_id = f"spec_{spec_hash[:12]}"
        spec_dir = self._spec_dir(frozen_spec_id)
        frozen_verifier_paths: dict[str, str] = {}

        for artifact in verifier_artifacts:
            artifact_path = Path(artifact).resolve()
            rel_path = relative_artifact_path(source_root, artifact_path)
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
            if record.score_report is None:
                return None
            return record.score_report.aggregate_score

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
            self._history_candidate_payload(record, frozen.spec.metric_name) for record in selected
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
        if run.state not in {RunState.RUNNING, RunState.WAITING_FOR_WORKERS, RunState.SELECTING}:
            raise RuntimeError(f"cannot plan next batch from state {run.state}")

        frozen = self._load_frozen_spec(run.frozen_spec_id)
        spec = frozen.spec
        remaining = max(0, spec.budget.max_candidates - run.candidates_total)
        planned_k = min(requested_k, remaining)
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
        run = self._load_run(run_id)
        if run.state not in {RunState.RUNNING, RunState.WAITING_FOR_WORKERS, RunState.SELECTING}:
            raise RuntimeError(f"cannot create candidates from state {run.state}")

        frozen = self._load_frozen_spec(run.frozen_spec_id)
        plan = self._load_plan(run_id, plan_id)
        if plan.status != "planned":
            raise RuntimeError(f"plan {plan_id} has already been started")

        remaining = max(0, frozen.spec.budget.max_candidates - run.candidates_total)
        if remaining <= 0 or plan.planned_k <= 0:
            return []

        if plan.requires_agent_proposals:
            if not proposals:
                raise ValueError("this strategy plan requires candidate proposals")
            self._validate_agent_proposals(plan, proposals)
            candidate_proposals = proposals[: min(plan.planned_k, remaining)]
        else:
            if proposals:
                raise ValueError("this strategy plan already contains fixed work orders")
            candidate_proposals = [
                self._proposal_from_work_order(work_order) for work_order in plan.work_orders
            ][: min(plan.planned_k, remaining)]

        tasks: list[CandidateTask] = []
        for index, proposal in enumerate(candidate_proposals, start=1):
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
    ) -> AgentSessionRecord:
        """Create a context/provenance handle for a candidate and return the
        OpenCode Task launch fields. Does not start a worker and does not track
        lifecycle state. The main agent must launch the worker via OpenCode Task
        using the returned ``launch`` payload.
        """
        run = self._load_run(run_id)
        if run.state not in {RunState.RUNNING, RunState.WAITING_FOR_WORKERS, RunState.SELECTING}:
            raise RuntimeError(f"cannot start agent session from state {run.state}")
        frozen = self._load_frozen_spec(run.frozen_spec_id)

        candidate_record = self._load_candidate_record(run_id, candidate_id)
        workspace = candidate_record.task.workspace

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
        )
        host = frozen.spec.strategy.worker_host
        host_handle = AgentHostHandle(host=host)
        if host == "codex":
            host_handle = host_handle.model_copy(
                update={"task_name": launch.get("task_name")}
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
    ) -> AgentSessionRecord:
        """Return an OpenCode Task launch payload that continues a prior session.

        This is not a fork and does not create a new candidate workspace. It
        reuses the existing runtime agent_session_id, candidate_id, workspace,
        and the bound OpenCode session id as Task's `task_id`.
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
        try:
            launch = self._build_continue_launch_payload(
                frozen=frozen,
                session=session,
                directive=normalized_directive,
                candidate_record=candidate_record,
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
        without it. Records an IterationReport for each call.
        """
        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        record = self._load_candidate_record(run_id, candidate_id)
        if record.status not in {"created", "evaluated"}:
            raise RuntimeError(
                f"cannot verify candidate in status {record.status}"
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
                commands = (
                    frozen.spec.process_verifiers
                    if scope == "process"
                    else frozen.spec.promotion_verifiers
                )
                if not commands:
                    commands = frozen.spec.process_verifiers
                report = self._run_commands(run, frozen, record, commands, scope)

            with self._run_transaction(run_id):
                run = self._load_run(run_id)
                record = self._load_candidate_record(run_id, candidate_id)
                record.detected_changed_files = detected_changed
                record.touched_denied_files = touched_denied
                record.changed_outside_allowed = outside_allowed
                record.status = "evaluated"
                record.score_report = report
                record.iterations.append(
                    IterationRecord(
                        iteration=len(record.iterations) + 1,
                        agent_session_id=agent_session_id,
                        score=report.aggregate_score,
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
                        metrics={r.name: r.metrics for r in report.verifier_results},
                        created_at=utc_timestamp(),
                    )
                )
                self._write_candidate_record(run_id, record)
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
            with self._run_transaction(run_id):
                run = self._load_run(run_id)
                run.state = RunState.FAILED
                self._write_run(run)
            raise

    def select(self, run_id: str, strategy: str = "independent_branches") -> dict[str, Any]:
        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        records = self._load_candidate_records(run_id)
        scored = [
            record
            for record in records
            if record.score_report
            and record.score_report.process_passed
            and record.score_report.aggregate_score is not None
        ]
        if not scored:
            raise RuntimeError("no verified candidates available for selection")

        reverse = frozen.spec.metric_direction == "maximize"
        selected = sorted(scored, key=lambda r: r.score_report.aggregate_score, reverse=reverse)[0]  # type: ignore[union-attr,return-value]
        run.state = RunState.READY_TO_PROMOTE
        run.selected_candidate_id = selected.candidate_id
        run.best_candidate_id = selected.candidate_id
        run.best_score = selected.score_report.aggregate_score
        self._write_run(run)
        return {
            "strategy": strategy,
            "selected_candidate_id": selected.candidate_id,
            "selected_score": selected.score_report.aggregate_score,
            "best_candidate_id": run.best_candidate_id,
            "best_score": run.best_score,
        }

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
                "| Candidate | Plan | Agent Sessions | Parent/Base | Status | Score | Process | Summary | Key Metrics | Changed Files |",
                "|---|---|---|---|---|---:|---|---|---|---|",
            ]
        )
        for record in records:
            score = ""
            passed = ""
            if record.score_report:
                score = "" if record.score_report.aggregate_score is None else str(record.score_report.aggregate_score)
                passed = str(record.score_report.process_passed)
            payload = self._history_candidate_payload(record, frozen.spec.metric_name)
            key_metrics = ", ".join(
                f"{key}={value}" for key, value in payload["key_metrics"].items()
            )
            changed = ", ".join(record.detected_changed_files)
            agent_sessions = ", ".join(
                session["agent_session_id"] for session in payload["agent_sessions"]
            )
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
                f"{self._markdown_cell(parent_base)} | {record.status} | {score} | {passed} | "
                f"{self._markdown_cell(payload['summary'])} | "
                f"{self._markdown_cell(key_metrics)} | {self._markdown_cell(changed)} |"
            )
        agent_sessions = self._load_agent_sessions(run_id)
        if agent_sessions:
            lines.extend(
                [
                    "",
                    "## Agent Sessions",
                    "",
                    (
                        "| Session | Host | Handle / OpenCode Session | Candidate | Verifier Runs | "
                        "Created | Updated |"
                    ),
                    "|---|---|---|---|---:|---|---|",
                ]
            )
            for session in agent_sessions:
                lines.append(
                    f"| `{session.agent_session_id}` | "
                    f"`{session.host}` | "
                    f"{self._markdown_cell(self._display_host_handle(session))} | "
                    f"`{session.candidate_id or ''}` | "
                    f"{session.counters.get('verifier_runs', 0)} | "
                    f"{session.created_at} | {session.updated_at} |"
                )
        lines.append("")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path

    def promote(self, run_id: str, candidate_id: str) -> Path:
        run = self._load_run(run_id)
        record = self._load_candidate_record(run_id, candidate_id)
        if not record.score_report or not record.score_report.process_passed:
            raise RuntimeError("cannot promote candidate without a passing score report")
        if record.touched_denied_files or record.changed_outside_allowed:
            raise RuntimeError("cannot promote candidate that changed denied/out-of-surface files")

        promotion_dir = self._run_dir(run_id) / "promotion"
        promotion_dir.mkdir(parents=True, exist_ok=True)
        patch_path = promotion_dir / f"{candidate_id}.patch"
        self._write_patch(Path(run.source_path), record.task.workspace, record.detected_changed_files, patch_path)
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
        if strategy.worker_budget is not None:
            if (
                strategy.worker_host == "codex"
                and strategy.worker_budget.max_runtime_seconds is None
            ):
                raise ValueError(
                    "codex worker_budget requires max_runtime_seconds so the "
                    "parent agent can enforce a watchdog deadline"
                )
            if (
                strategy.worker_host == "claude-code"
                and strategy.worker_budget.max_turns is None
            ):
                raise ValueError(
                    "claude-code worker_budget requires max_turns so the "
                    "subagent definition can enforce a turn budget"
                )
            if strategy.worker_host == "claude-code":
                turns = strategy.worker_budget.max_turns
                configured_agent = strategy.worker_agent_type
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

    def _worker_policy(self, strategy: StrategySpec) -> dict[str, Any]:
        adapter = get_agent_host_adapter(strategy.worker_host)
        worker_agent_type = strategy.worker_agent_type
        worker_budget = self._worker_budget_dict(strategy)
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
            "supports_bind_handle": adapter.capabilities.supports_bind_handle,
            "supports_same_worker_continue": adapter.capabilities.supports_same_worker_continue,
            "supports_trace_export": adapter.capabilities.supports_trace_export,
            "uses_background_workers": adapter.capabilities.uses_background_workers,
            "directive_rule": (
                "Worker directives should describe the candidate idea and deliverable, not score "
                "targets or baseline scores. Workers must treat any score target in a directive as "
                "main-agent context only and must not run local scoring to satisfy it."
            ),
            "requires_agent_session": True,
            "direct_edit_allowed": False,
            "reason": (
                f"worker_mode=agent-session-pool requires the main agent to launch "
                f"{strategy.worker_host} foreground workers using the launch payload "
                "from search_start_agent_session."
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
            return "any_search_agent"
        if host == "claude-code":
            return "any-search-agent"
        return "AnySearchAgent"

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

    def _build_launch_payload(
        self,
        frozen: FrozenSpec,
        candidate_id: str,
        agent_session_id: str,
        directive: dict[str, Any],
        candidate_record: CandidateRecord,
    ) -> dict[str, Any]:
        worker_agent_type = self._candidate_worker_agent_type(frozen, candidate_record)
        proposal = candidate_record.task.proposal
        if directive.get("goal"):
            short_intent = str(directive["goal"])
        elif proposal is not None and proposal.intent:
            short_intent = proposal.intent
        else:
            short_intent = candidate_record.task.hypothesis

        if directive:
            idea_lines = [f"{key}: {value}" for key, value in directive.items()]
        elif proposal is not None and proposal.intent:
            idea_lines = [proposal.intent]
            if proposal.expected_tradeoff:
                idea_lines.append(f"expected_tradeoff: {proposal.expected_tradeoff}")
        else:
            idea_lines = [candidate_record.task.hypothesis]
        one_paragraph_idea = "; ".join(idea_lines)

        adapter = get_agent_host_adapter(frozen.spec.strategy.worker_host)
        return adapter.build_launch_payload(
            worker_agent_type=worker_agent_type,
            candidate_id=candidate_id,
            agent_session_id=agent_session_id,
            short_intent=short_intent,
            one_paragraph_idea=one_paragraph_idea,
            worker_budget=self._candidate_worker_budget(frozen, candidate_record),
        )

    def _build_continue_launch_payload(
        self,
        frozen: FrozenSpec,
        session: AgentSessionRecord,
        directive: dict[str, Any],
        candidate_record: CandidateRecord,
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
        scored = self._scored_records(records)

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
        scored = self._records_by_created(self._scored_records(records))
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
                            "search-runtime_search_run_verifier."
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
        scored = self._scored_records(records)
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
        scored = self._scored_records(records)

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

        if base_candidate_id:
            base_record = self._load_candidate_record(run.run_id, base_candidate_id)
            copy_source_tree(base_record.task.workspace, workspace)
        else:
            copy_source_tree(Path(run.source_path), workspace)
        scratch_dir = workspace / ".tmp"
        scratch_dir.mkdir(parents=True, exist_ok=True)

        instructions = [
            "Work only inside this candidate workspace.",
            "Use this workspace's .tmp/ directory for notes and scratch drafts.",
            "Do not use /tmp, home directories, or paths outside the candidate workspace for candidate work.",
            "Modify only files listed in allowed_files; never touch denied_files or frozen verifier artifacts.",
            "Do not delete, move, or clean files; destructive commands such as rm, mv, rmdir, unlink, trash, and find -delete are forbidden.",
            "You may git init, git add, git commit, git reset, git restore, and git checkout INSIDE this workspace to advance and revert iterations.",
            "All scoring must go through search-runtime_search_run_verifier; do not run the process_verifiers command directly via bash, and do not write your own scorer.",
            "Pass context.agent_session_id to search_run_verifier so the runtime can record iteration provenance.",
            "Iterate freely within your OpenCode step budget; each run_verifier call records an iteration. When steps run out OpenCode will ask you to summarize and stop.",
            "Inside the workspace, git init and use git commit to mark iterations that improved, and git reset --hard HEAD~1 to discard iterations that regressed.",
            "Maintain an iteration log at workspace/.tmp/results.tsv with header: commit \\t <metric_name> \\t status \\t hypothesis (use the literal context.metric_name value as the column-2 header). Commit each iteration before verifying so the commit hash is real; on discard, reset with git reset --hard HEAD~1 (the discarded hash stays recoverable via git reflog).",
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
                self._history_candidate_payload(record, frozen.spec.metric_name)
                for record in selected_records
            ],
            "description": (
                "Official runtime-selected history view for the current strategy plan."
            ),
        }

    def _scored_records(self, records: list[CandidateRecord]) -> list[CandidateRecord]:
        return [
            record
            for record in records
            if record.score_report
            and record.score_report.process_passed
            and record.score_report.aggregate_score is not None
        ]

    def _best_record(self, records: list[CandidateRecord], spec: SearchSpec) -> CandidateRecord:
        reverse = spec.metric_direction == "maximize"
        return sorted(records, key=lambda record: record.score_report.aggregate_score, reverse=reverse)[0]  # type: ignore[union-attr]

    def _top_records(
        self,
        records: list[CandidateRecord],
        spec: SearchSpec,
        top_n: int,
    ) -> list[CandidateRecord]:
        scored = self._scored_records(records)
        if not scored:
            return self._records_by_created(records)[:top_n]
        reverse = spec.metric_direction == "maximize"
        return sorted(scored, key=lambda record: record.score_report.aggregate_score, reverse=reverse)[:top_n]  # type: ignore[union-attr]

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
                    },
                    failure_class="EditSurfaceViolation",
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
            hardcoding_suspected=True,
        )

    def _run_commands(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        record: CandidateRecord,
        commands: list[VerifierCommand],
        scope: str,
    ) -> ScoreReport:
        results = [self._run_command(run, frozen, record, command) for command in commands]
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
    ) -> VerifierResult:
        if command.command[0] == "search-runtime-internal":
            return self._run_internal_command(frozen, record, command)

        logs_dir = self._run_dir(run.run_id) / "candidates" / record.candidate_id / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{command.name}.log"
        cwd = (record.task.workspace / command.cwd).resolve()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(cwd) + os.pathsep + env.get("PYTHONPATH", "")
        start = time.perf_counter()
        try:
            completed = subprocess.run(
                command.command,
                cwd=cwd,
                env=env,
                text=True,
                capture_output=True,
                timeout=command.timeout_seconds,
                check=False,
            )
            elapsed = time.perf_counter() - start
            metrics = self._parse_metrics(completed.stdout)
            metrics.setdefault("returncode", completed.returncode)
            metrics.setdefault("elapsed_seconds", elapsed)
            passed = completed.returncode == 0 and "error" not in metrics
            score = self._score_from_metrics(frozen.spec.metric_name, metrics)
            log_path.write_text(
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
                failure_class=None if passed else "VerifierCommandFailed",
            )
        except subprocess.TimeoutExpired as exc:
            log_path.write_text(str(exc), encoding="utf-8")
            return VerifierResult(
                name=command.name,
                role=command.role,
                passed=False,
                score=0.0,
                metrics={"timeout_seconds": command.timeout_seconds},
                log_path=log_path,
                failure_class="Timeout",
            )

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

    def _score_from_metrics(self, metric_name: str, metrics: dict[str, Any]) -> float | None:
        for key in (metric_name, "combined_score", "score", "overall_score"):
            value = metrics.get(key)
            if isinstance(value, int | float):
                return float(value)
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

    def _history_candidate_payload(self, record: CandidateRecord, metric_name: str) -> dict[str, Any]:
        score_report = record.score_report
        metrics: dict[str, Any] = {}
        verifier_summaries: list[dict[str, Any]] = []
        failure_classes: list[str] = []
        log_paths: list[str] = []

        if score_report:
            for result in score_report.verifier_results:
                if not metrics and result.metrics:
                    metrics = result.metrics
                if result.failure_class:
                    failure_classes.append(result.failure_class)
                if result.log_path:
                    log_paths.append(str(result.log_path))
                verifier_summaries.append(
                    {
                        "name": result.name,
                        "role": result.role,
                        "passed": result.passed,
                        "score": result.score,
                        "failure_class": result.failure_class,
                        "log_path": str(result.log_path) if result.log_path else None,
                    }
                )

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
            "agent_sessions": self._agent_session_payloads_for_candidate(
                record.task.run_id,
                record.candidate_id,
            ),
            "summary": "",
            "next_ideas": [],
            "risk_notes": [],
            "artifact_status": None,
            "changed_files": record.detected_changed_files,
            "touched_denied_files": record.touched_denied_files,
            "changed_outside_allowed": record.changed_outside_allowed,
            "process_passed": score_report.process_passed if score_report else None,
            "score": score_report.aggregate_score if score_report else None,
            "metric_name": metric_name,
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
        source_hashes = self._hash_tree(source)
        workspace_hashes = self._hash_tree(workspace)
        changed: list[str] = []
        for rel_path in sorted(set(source_hashes) | set(workspace_hashes)):
            if source_hashes.get(rel_path) != workspace_hashes.get(rel_path):
                changed.append(rel_path)
        return changed

    def _hash_tree(self, root: Path) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for path in list_files(root):
            rel_path = path.relative_to(root).as_posix()
            hashes[rel_path] = sha256_file(path)
        return hashes

    def _write_patch(
        self,
        source: Path,
        workspace: Path,
        changed_files: list[str],
        patch_path: Path,
    ) -> None:
        chunks: list[str] = []
        for rel_path in changed_files:
            src = source / rel_path
            dst = workspace / rel_path
            src_lines = src.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if src.exists() else []
            dst_lines = dst.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if dst.exists() else []
            chunks.extend(
                difflib.unified_diff(
                    src_lines,
                    dst_lines,
                    fromfile=f"a/{rel_path}",
                    tofile=f"b/{rel_path}",
                )
            )
        patch_path.write_text("".join(chunks), encoding="utf-8")

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
        return FrozenSpec.model_validate(load_json(self._spec_dir(frozen_spec_id) / "frozen_spec.json"))

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
