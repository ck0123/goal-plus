from __future__ import annotations

import difflib
import calendar
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

from agentic_any_search_mcp.models import (
    AgentObservation,
    AgentSessionBudget,
    AgentSessionEvent,
    AgentSessionPhase,
    AgentSessionRecord,
    AgentSessionStatus,
    AgentSessionWaitResult,
    ArtifactBundle,
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
    ScoreReport,
    SearchPlan,
    SearchSpec,
    StrategySpec,
    TERMINAL_AGENT_SESSION_STATUSES,
    VerifierCommand,
    VerifierResult,
    VerifierRole,
    VisibilityMode,
)


IGNORED_NAMES = {".git", ".search", ".tmp", ".pytest_cache", "__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


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
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    tmp_path.replace(path)


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
    def __init__(self, root_dir: Path | str = ".search") -> None:
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
        (self._run_dir(run_id) / "agent_events").mkdir(parents=True, exist_ok=True)
        (self._run_dir(run_id) / "observations").mkdir(parents=True, exist_ok=True)
        return run_id

    def status(self, run_id: str) -> RunSummary:
        run = self._load_run(run_id)
        records = self._load_candidate_records(run_id)
        running = sum(1 for record in records if record.status in {"created", "submitted"})
        evaluated = sum(1 for record in records if record.status == "evaluated")
        return RunSummary(
            run_id=run.run_id,
            state=run.state,
            frozen_spec_id=run.frozen_spec_id,
            candidates_total=len(records),
            candidates_running=running,
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
            "worker_policy": self._worker_policy(frozen.spec.strategy),
            "best_candidate_id": run.best_candidate_id,
            "best_score": run.best_score,
            "total_candidates": len(records),
            "returned_candidates": len(candidates),
            "top_n": top_n,
            "sort_by": sort_by,
            "candidates": candidates,
        }

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
        mode = self._strategy_mode(strategy)

        if strategy.driver != "builtin":
            plan = self._plan_custom_strategy(run, frozen, requested_k, planned_k, remaining)
        elif mode in {"agent", "agent_guided"}:
            plan = self._plan_agent_guided(run, frozen, requested_k, planned_k, remaining)
        elif mode in {"evolve", "evolve_mode", "openevolve"}:
            plan = self._plan_evolve(run, frozen, requested_k, planned_k, remaining)
        elif mode in {"mcts", "mcts_mode"}:
            plan = self._plan_mcts(run, frozen, requested_k, planned_k, remaining)
        elif mode in {"independent", "independent_branches"}:
            plan = self._plan_independent(run, frozen, requested_k, planned_k, remaining)
        else:
            raise ValueError(f"unknown builtin strategy: {strategy.name}")

        plan.worker_policy = self._worker_policy(plan.strategy)
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

    def next_batch(self, run_id: str, k: int) -> list[CandidateTask]:
        if k <= 0:
            raise ValueError("k must be > 0")

        plan = self.plan_next(run_id, k)
        if plan.requires_agent_proposals:
            raise RuntimeError(
                "current strategy requires proposals; call search_plan_next and search_start_batch"
            )
        return self.start_batch(run_id, plan.plan_id)

    def start_agent_session(
        self,
        run_id: str,
        candidate_id: str | None = None,
        directive: dict[str, Any] | str | None = None,
        budget: dict[str, Any] | None = None,
        visibility_mode: str = "observations",
    ) -> AgentSessionRecord:
        run = self._load_run(run_id)
        if run.state not in {RunState.RUNNING, RunState.WAITING_FOR_WORKERS, RunState.SELECTING}:
            raise RuntimeError(f"cannot start agent session from state {run.state}")
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        self._refresh_agent_session_deadlines(run_id)
        if self._run_deadline_reached(run, frozen):
            self.abort_all_agent_sessions(run_id, "run budget exhausted")
            raise RuntimeError("run budget exhausted")

        active_count = self._active_agent_session_count(run_id)
        if active_count >= frozen.spec.budget.max_parallel:
            raise RuntimeError("agent session pool is full")

        candidate_record: CandidateRecord | None = None
        workspace: Path | None = None
        if candidate_id is not None:
            candidate_record = self._load_candidate_record(run_id, candidate_id)
            workspace = candidate_record.task.workspace

        requested_budget = dict(budget or {})
        remaining_seconds = self._remaining_run_seconds(run, frozen)
        if remaining_seconds <= 0:
            self.abort_all_agent_sessions(run_id, "run budget exhausted")
            raise RuntimeError("run budget exhausted")
        requested_wall_seconds = int(
            requested_budget.get("max_wall_seconds")
            or frozen.spec.budget.max_worker_seconds
            or frozen.spec.strategy.worker_timeout_seconds
        )
        if requested_wall_seconds <= 0:
            raise ValueError("max_wall_seconds must be > 0")
        max_wall_seconds = max(1, min(requested_wall_seconds, remaining_seconds))
        deadline_epoch = min(time.time() + max_wall_seconds, self._run_deadline_epoch(run, frozen))
        deadline_at = utc_timestamp_from_epoch(deadline_epoch)

        session_budget = AgentSessionBudget.model_validate(
            {
                "max_wall_seconds": max_wall_seconds,
                "deadline_at": deadline_at,
                "max_steps": requested_budget.get("max_steps"),
                "max_tool_calls": requested_budget.get("max_tool_calls"),
                "max_verifier_runs": requested_budget.get(
                    "max_verifier_runs",
                    frozen.spec.strategy.worker_local_verifier_max_runs,
                ),
                "heartbeat_interval_seconds": requested_budget.get("heartbeat_interval_seconds", 30),
                "stale_after_seconds": requested_budget.get("stale_after_seconds", 90),
                "finalize_before_seconds": requested_budget.get("finalize_before_seconds", 30),
                "grace_seconds": requested_budget.get("grace_seconds", 30),
            }
        )

        agent_session_id = self._make_agent_session_id(run_id, run.next_agent_session_index)
        run.next_agent_session_index += 1
        now = utc_timestamp()
        session = AgentSessionRecord(
            agent_session_id=agent_session_id,
            run_id=run_id,
            candidate_id=candidate_id,
            created_at=now,
            updated_at=now,
            last_heartbeat_at=now,
            status=AgentSessionStatus.RUNNING.value,
            phase=AgentSessionPhase.PROBING.value,
            visibility_mode=VisibilityMode(visibility_mode),
            directive=self._normalize_main_directive(directive),
            workspace=workspace,
            budget=session_budget,
            current_goal=self._normalize_main_directive(directive).get("goal", ""),
        )
        self._write_run(run)
        self._write_agent_session(session)
        self._append_agent_event(
            run_id,
            "agent_started",
            agent_session_id,
            {
                "candidate_id": candidate_id,
                "deadline_at": session_budget.deadline_at,
                "max_wall_seconds": session_budget.max_wall_seconds,
                "active_count": active_count + 1,
                "max_concurrent_agents": frozen.spec.budget.max_parallel,
            },
        )
        return session

    def get_agent_context(self, agent_session_id: str) -> dict[str, Any]:
        session = self._load_agent_session_by_id(agent_session_id)
        run = self._load_run(session.run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        candidate_record = (
            self._load_candidate_record(session.run_id, session.candidate_id)
            if session.candidate_id
            else None
        )
        return {
            "agent_session_id": session.agent_session_id,
            "run_id": session.run_id,
            "candidate_id": session.candidate_id,
            "status": session.status,
            "phase": session.phase,
            "visibility_mode": session.visibility_mode,
            "directive": session.directive,
            "budget": session.budget.model_dump(mode="json"),
            "workspace": str(session.workspace) if session.workspace else None,
            "objective": frozen.spec.objective,
            "metric_name": frozen.spec.metric_name,
            "metric_direction": frozen.spec.metric_direction,
            "run_budget": frozen.spec.budget.model_dump(mode="json"),
            "run_deadline_at": utc_timestamp_from_epoch(self._run_deadline_epoch(run, frozen)),
            "candidate_task": candidate_record.task.model_dump(mode="json") if candidate_record else None,
            "history": self.list_history(session.run_id, top_n=5, sort_by="score"),
            "peer_status": [
                peer.model_dump(mode="json")
                for peer in self.list_agent_status(session.run_id)
                if peer.agent_session_id != session.agent_session_id
            ],
            "observations": self.list_observations(session.run_id, top_n=20),
        }

    def update_agent_status(
        self,
        agent_session_id: str,
        phase: str,
        current_goal: str = "",
        last_action: str = "",
        next_step: str = "",
        blockers: list[str] | None = None,
        status: str | None = None,
        heartbeat: bool = True,
    ) -> AgentSessionRecord:
        session = self._load_agent_session_by_id(agent_session_id)
        if session.status in TERMINAL_AGENT_SESSION_STATUSES:
            raise RuntimeError(f"cannot update terminal agent session {agent_session_id}")

        now = utc_timestamp()
        next_phase = AgentSessionPhase(phase)
        if status is not None:
            next_status = AgentSessionStatus(status).value
        elif next_phase == AgentSessionPhase.BLOCKED:
            next_status = AgentSessionStatus.BLOCKED.value
        elif session.status == AgentSessionStatus.BLOCKED.value and next_phase != AgentSessionPhase.BLOCKED:
            next_status = AgentSessionStatus.RUNNING.value
        else:
            next_status = session.status

        updated = session.model_copy(
            update={
                "phase": next_phase.value,
                "status": next_status,
                "current_goal": current_goal,
                "last_action": last_action,
                "next_step": next_step,
                "blockers": blockers or [],
                "updated_at": now,
                "last_heartbeat_at": now if heartbeat else session.last_heartbeat_at,
            }
        )
        self._write_agent_session(updated)
        self._append_agent_event(
            session.run_id,
            "agent_blocked" if next_status == AgentSessionStatus.BLOCKED.value else "agent_status_updated",
            agent_session_id,
            {
                "phase": next_phase.value,
                "status": next_status,
                "current_goal": current_goal,
                "last_action": last_action,
                "next_step": next_step,
                "blockers": blockers or [],
            },
        )
        return updated

    def list_agent_status(self, run_id: str, include_stale: bool = True) -> list[AgentSessionRecord]:
        self._refresh_agent_session_deadlines(run_id)
        sessions = self._load_agent_sessions(run_id)
        if include_stale:
            return sessions
        return [session for session in sessions if not self._agent_session_is_stale(session)]

    def finish_agent_session(
        self,
        agent_session_id: str,
        status: str = "completed",
        summary: str = "",
        result: dict[str, Any] | None = None,
    ) -> AgentSessionRecord:
        if status not in {"completed", "failed"}:
            raise ValueError("finish status must be 'completed' or 'failed'")
        session = self._load_agent_session_by_id(agent_session_id)
        if session.status in TERMINAL_AGENT_SESSION_STATUSES:
            return session
        now = utc_timestamp()
        next_status = AgentSessionStatus(status)
        updated = session.model_copy(
            update={
                "status": next_status.value,
                "phase": AgentSessionPhase.IDLE.value,
                "summary": summary,
                "result": result or {},
                "updated_at": now,
                "last_heartbeat_at": now,
            }
        )
        self._write_agent_session(updated)
        self._append_agent_event(
            session.run_id,
            "agent_completed" if next_status == AgentSessionStatus.COMPLETED else "agent_failed",
            agent_session_id,
            {"summary": summary, "result": result or {}},
        )
        return updated

    def request_agent_finalize(self, agent_session_id: str, reason: str = "") -> AgentSessionRecord:
        session = self._load_agent_session_by_id(agent_session_id)
        if session.status in TERMINAL_AGENT_SESSION_STATUSES:
            return session
        now = utc_timestamp()
        updated = session.model_copy(
            update={
                "status": AgentSessionStatus.FINALIZING.value,
                "phase": AgentSessionPhase.FINALIZING.value,
                "next_step": "submit best-so-far candidate or summarize why no candidate is available",
                "updated_at": now,
                "last_heartbeat_at": now,
            }
        )
        self._write_agent_session(updated)
        self._append_agent_event(
            session.run_id,
            "agent_finalize_requested",
            agent_session_id,
            {"reason": reason},
        )
        return updated

    def abort_agent_session(self, agent_session_id: str, reason: str = "") -> AgentSessionRecord:
        session = self._load_agent_session_by_id(agent_session_id)
        return self._abort_agent_session_record(session, reason)

    def _abort_agent_session_record(
        self,
        session: AgentSessionRecord,
        reason: str = "",
    ) -> AgentSessionRecord:
        if session.status in TERMINAL_AGENT_SESSION_STATUSES:
            return session
        now = utc_timestamp()
        updated = session.model_copy(
            update={
                "status": AgentSessionStatus.ABORTED.value,
                "phase": AgentSessionPhase.IDLE.value,
                "summary": reason,
                "updated_at": now,
                "last_heartbeat_at": now,
            }
        )
        self._write_agent_session(updated)
        self._append_agent_event(
            session.run_id,
            "agent_aborted",
            session.agent_session_id,
            {"reason": reason},
        )
        return updated

    def abort_all_agent_sessions(self, run_id: str, reason: str = "") -> list[AgentSessionRecord]:
        aborted: list[AgentSessionRecord] = []
        for session in self._load_agent_sessions(run_id):
            if session.status not in TERMINAL_AGENT_SESSION_STATUSES:
                aborted.append(self._abort_agent_session_record(session, reason))
        return aborted

    def record_agent_step(
        self,
        agent_session_id: str,
        steps_delta: int = 0,
        tool_calls_delta: int = 0,
        verifier_runs_delta: int = 0,
        tokens_delta: int = 0,
    ) -> AgentSessionRecord:
        if min(steps_delta, tool_calls_delta, verifier_runs_delta, tokens_delta) < 0:
            raise ValueError("counter deltas must be non-negative")
        session = self._load_agent_session_by_id(agent_session_id)
        if session.status in TERMINAL_AGENT_SESSION_STATUSES:
            raise RuntimeError(f"cannot update terminal agent session {agent_session_id}")
        counters = dict(session.counters)
        counters["steps"] = counters.get("steps", 0) + steps_delta
        counters["tool_calls"] = counters.get("tool_calls", 0) + tool_calls_delta
        counters["verifier_runs"] = counters.get("verifier_runs", 0) + verifier_runs_delta
        counters["tokens"] = counters.get("tokens", 0) + tokens_delta
        updated = session.model_copy(update={"counters": counters, "updated_at": utc_timestamp()})
        self._write_agent_session(updated)
        self._append_agent_event(
            session.run_id,
            "agent_budget_updated",
            agent_session_id,
            {"counters": counters},
        )
        if updated.budget.max_steps is not None and counters["steps"] >= updated.budget.max_steps:
            return self.request_agent_finalize(agent_session_id, "max_steps reached")
        if updated.budget.max_tool_calls is not None and counters["tool_calls"] >= updated.budget.max_tool_calls:
            return self.request_agent_finalize(agent_session_id, "max_tool_calls reached")
        if counters["verifier_runs"] > updated.budget.max_verifier_runs:
            return self.request_agent_finalize(agent_session_id, "max_verifier_runs exceeded")
        return updated

    def publish_observation(
        self,
        agent_session_id: str,
        summary: str,
        evidence: str = "",
        next_ideas: list[str] | None = None,
        tags: list[str] | None = None,
        visibility: str = "observations",
    ) -> AgentObservation:
        session = self._load_agent_session_by_id(agent_session_id)
        run = self._load_run(session.run_id)
        observation_id = f"obs_{run.next_observation_index:06d}"
        run.next_observation_index += 1
        observation = AgentObservation(
            observation_id=observation_id,
            run_id=session.run_id,
            agent_session_id=agent_session_id,
            created_at=utc_timestamp(),
            summary=summary,
            evidence=evidence,
            next_ideas=next_ideas or [],
            tags=tags or [],
            visibility=VisibilityMode(visibility),
        )
        self._write_run(run)
        self._write_observation(observation)
        self._append_agent_event(
            session.run_id,
            "observation_published",
            agent_session_id,
            {"observation_id": observation_id, "summary": summary, "tags": tags or []},
        )
        return observation

    def list_observations(
        self,
        run_id: str,
        visibility: str | None = None,
        tags: list[str] | None = None,
        top_n: int = 20,
    ) -> list[dict[str, Any]]:
        if top_n <= 0:
            raise ValueError("top_n must be > 0")
        observations = self._load_observations(run_id)
        if visibility is not None:
            mode = VisibilityMode(visibility).value
            observations = [obs for obs in observations if obs.visibility == mode]
        if tags:
            required = set(tags)
            observations = [obs for obs in observations if required.intersection(obs.tags)]
        return [obs.model_dump(mode="json") for obs in observations[-top_n:]]

    def wait_agent_events(
        self,
        run_id: str,
        timeout_seconds: int = 300,
        wake_on: list[str] | None = None,
        since_event_id: str | None = None,
    ) -> AgentSessionWaitResult:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be >= 0")
        wake_set = set(
            wake_on
            or [
                "agent_completed",
                "agent_failed",
                "agent_blocked",
                "agent_aborted",
                "agent_timed_out",
                "run_deadline",
            ]
        )
        deadline = time.time() + timeout_seconds
        while True:
            run_deadline_reached = self._enforce_run_deadline(run_id)
            self._refresh_agent_session_deadlines(run_id)
            event_log = self._load_agent_events(run_id)
            events = [
                event
                for event in event_log
                if event.type in wake_set
                and (since_event_id is None or event.event_id > since_event_id)
            ]
            if events or run_deadline_reached or timeout_seconds == 0 or time.time() >= deadline:
                sessions = self._load_agent_sessions(run_id)
                frozen = self._load_frozen_spec(self._load_run(run_id).frozen_spec_id)
                return AgentSessionWaitResult(
                    run_id=run_id,
                    timed_out=not events and not run_deadline_reached,
                    run_deadline_reached=run_deadline_reached,
                    last_event_id=event_log[-1].event_id if event_log else since_event_id,
                    events=events,
                    sessions=sessions,
                    active_count=self._active_agent_session_count(run_id),
                    max_concurrent_agents=frozen.spec.budget.max_parallel,
                )
            time.sleep(min(0.1, max(0.0, deadline - time.time())))

    def submit_candidate(
        self,
        run_id: str,
        candidate_id: str,
        artifact: ArtifactBundle,
    ) -> None:
        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        record = self._load_candidate_record(run_id, candidate_id)
        if artifact.candidate_id != candidate_id:
            raise ValueError("artifact candidate_id does not match candidate_id")
        worker_policy = self._worker_policy(frozen.spec.strategy)
        if worker_policy["requires_agent_session"] and not artifact.agent_session_id:
            raise ValueError(
                "candidate artifact must include agent_session_id for worker_mode=agent-session-pool"
            )
        if artifact.agent_session_id:
            session = self._load_agent_session_by_id(artifact.agent_session_id, run_id=run_id)
            if session.run_id != run_id or session.candidate_id != candidate_id:
                raise ValueError("artifact agent_session_id does not belong to this candidate")
            if session.status in {
                AgentSessionStatus.ABORTED.value,
                AgentSessionStatus.TIMED_OUT.value,
            }:
                raise RuntimeError(
                    f"cannot submit artifact from {session.status} agent session"
                )

        detected_changed = self._detect_changed_files(Path(run.source_path), record.task.workspace)
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

        record.status = "submitted"
        record.artifact = artifact
        record.detected_changed_files = detected_changed
        record.touched_denied_files = touched_denied
        record.changed_outside_allowed = outside_allowed
        self._write_candidate_record(run_id, record)

    def run_verifier(
        self,
        run_id: str,
        candidate_id: str,
        scope: Literal["process", "promotion"] = "process",
        agent_session_id: str | None = None,
    ) -> ScoreReport:
        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        record = self._load_candidate_record(run_id, candidate_id)
        if record.status not in {"submitted", "evaluated"}:
            raise RuntimeError("candidate must be submitted before verification")

        session = None
        if agent_session_id:
            session = self._load_agent_session_by_id(agent_session_id, run_id=run_id)
            if session.candidate_id != candidate_id:
                raise ValueError(
                    "artifact agent_session_id does not belong to this candidate"
                )
            if session.status in TERMINAL_AGENT_SESSION_STATUSES:
                raise RuntimeError(
                    f"cannot verify from terminal agent session {agent_session_id}"
                )

        old_state = run.state
        run.state = RunState.EVALUATING
        self._write_run(run)

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

            record.status = "evaluated"
            record.score_report = report
            self._write_candidate_record(run_id, record)
            self._update_best_seen(run, frozen.spec, report)
            run.candidates_evaluated = len(
                [r for r in self._load_candidate_records(run_id) if r.status == "evaluated"]
            )
            if run.state == RunState.EVALUATING:
                run.state = RunState.RUNNING if old_state != RunState.READY_TO_PROMOTE else old_state
            self._write_run(run)

            if session is not None and agent_session_id is not None:
                counters = dict(session.counters)
                counters["verifier_runs"] = counters.get("verifier_runs", 0) + 1
                updated = session.model_copy(
                    update={"counters": counters, "updated_at": utc_timestamp()}
                )
                self._write_agent_session(updated)
                if (
                    updated.budget.max_verifier_runs is not None
                    and counters["verifier_runs"] >= updated.budget.max_verifier_runs
                ):
                    self.request_agent_finalize(
                        agent_session_id, "max_verifier_runs reached"
                    )

            return report
        except Exception:
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
        selected = sorted(scored, key=lambda r: r.score_report.aggregate_score, reverse=reverse)[0]
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
                    "| Session | Candidate | Status | Phase | Deadline | Summary |",
                    "|---|---|---|---|---|---|",
                ]
            )
            for session in agent_sessions:
                lines.append(
                    f"| `{session.agent_session_id}` | `{session.candidate_id or ''}` | "
                    f"{session.status} | {session.phase} | `{session.budget.deadline_at}` | "
                    f"{self._markdown_cell(session.summary)} |"
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

    def abort(self, run_id: str, reason: str) -> None:
        run = self._load_run(run_id)
        run.state = RunState.ABORTED
        run.budget_used["abort_reason"] = reason
        self._write_run(run)

    def _strategy_mode(self, strategy: StrategySpec) -> str:
        return strategy.name.strip().lower().replace("-", "_")

    def _worker_policy(self, strategy: StrategySpec) -> dict[str, Any]:
        mode = strategy.worker_mode
        if mode == "auto":
            mode = (
                "agent-session-pool"
                if self._strategy_mode(strategy) not in {"independent", "independent_branches"}
                else "main-agent-search-direct"
            )
        requires_agent_session = mode == "agent-session-pool"
        local_verifier_max_runs = strategy.worker_local_verifier_max_runs
        if local_verifier_max_runs == 0:
            local_validation_rule = (
                "Workers must not run the process verifier or any equivalent scoring/evaluation "
                "command. Workers may run non-scoring static checks such as py_compile. "
                "The main agent/runtime owns all actual verification after submission."
            )
        else:
            local_validation_rule = (
                f"Workers may run local verifier sanity checks at most {local_verifier_max_runs} "
                "times; runtime-owned verification after submission does not count against this "
                "local limit."
            )
        return {
            "mode": mode,
            "configured_mode": strategy.worker_mode,
            "worker_agent_type": strategy.worker_agent_type,
            "subagent_type": strategy.worker_agent_type,
            "timeout_seconds": strategy.worker_timeout_seconds,
            "local_verifier_max_runs": local_verifier_max_runs,
            "collection_rule": (
                "Collect or salvage a best-so-far artifact by the worker deadline; "
                "do not leave dispatched candidates unsubmitted."
            ),
            "directive_rule": (
                "Worker directives should describe the candidate idea and deliverable, not score "
                "targets or baseline scores. Workers must treat any score target in a directive as "
                "main-agent context only and must not run local scoring to satisfy it."
            ),
            "local_validation_rule": local_validation_rule,
            "requires_agent_session": requires_agent_session,
            "direct_edit_allowed": mode == "main-agent-search-direct",
            "supervisor_tools": [
                "search_start_agent_session",
                "search_wait_agent_events",
                "search_abort_agent_session",
                "search_abort_all_agent_sessions",
            ],
            "reason": (
                "worker_mode=agent-session-pool requires durable agent sessions and supervisor wait/abort control"
                if requires_agent_session
                else "worker_mode=main-agent-search-direct allows the host agent to edit candidate workspaces directly"
            ),
        }

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
            "Use this workspace's .tmp/ directory only for notes, static drafts, and non-scoring helper material.",
            "Do not create or run scratch experiment scripts, scorer clones, validation harnesses, parameter sweeps, or benchmark scripts.",
            "Do not use /tmp or other directories outside the candidate workspace for scratch work.",
            "Do not delete, move, reset, restore, or clean files; destructive commands such as rm, mv, rmdir, unlink, trash, find -delete, git clean, git reset, git restore, and git checkout are forbidden.",
            "Modify only allowed files.",
            "Do not modify frozen verifier files.",
            "Submit artifacts to the runtime; do not change the main workspace.",
        ]
        if plan.worker_policy.get("requires_agent_session"):
            instructions.append(
                "This run is configured with worker_mode=agent-session-pool; candidate execution must be tracked by search_start_agent_session and supervised with search_wait_agent_events."
            )
            instructions.append(
                f"Agent session wall-clock budget defaults to {plan.worker_policy['timeout_seconds']} seconds and is capped by the remaining run budget."
            )
            instructions.append(
                "Candidate artifacts must include the producing agent_session_id."
            )
            instructions.append(
                "Do not launch long-running foreground Task calls when supervision or abort is required; run workers as background/managed sessions so the supervisor can wait, inspect status, and abort."
            )
            if plan.worker_policy["local_verifier_max_runs"] == 0:
                instructions.append(
                    "Agent session must not run the process verifier or any equivalent local scorer; only non-scoring static checks such as py_compile are allowed."
                )
            else:
                instructions.append(
                    f"Agent session may run local verifier sanity checks at most {plan.worker_policy['local_verifier_max_runs']} times before submitting."
                )
            instructions.append(
                "Final candidate code must be bounded and fast; do not embed long searches, random restarts, or parameter sweeps in the final allowed file."
            )
            instructions.append(
                "If the session directive mentions score targets or baseline scores, treat them as context only and do not run local scoring to satisfy them."
            )
            if plan.worker_policy.get("subagent_type"):
                instructions.append(
                    f"Use subagent_type={plan.worker_policy['subagent_type']!r} for the managed/background agent session."
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
            stop_conditions={
                "max_worker_seconds": frozen.spec.budget.max_worker_seconds,
            },
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
        artifact = record.artifact
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
            "artifact_agent_session_id": artifact.agent_session_id if artifact else None,
            "summary": artifact.summary if artifact else "",
            "next_ideas": artifact.next_ideas if artifact else [],
            "risk_notes": artifact.risk_notes if artifact else [],
            "artifact_status": artifact.status if artifact else None,
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
                "status": session.status,
                "phase": session.phase,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "deadline_at": session.budget.deadline_at,
                "directive": session.directive,
                "summary": session.summary,
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

    def _candidate_dir(self, run_id: str, candidate_id: str) -> Path:
        return self._run_dir(run_id) / "candidates" / candidate_id

    def _plan_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "plans"

    def _agent_session_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "agent_sessions"

    def _agent_event_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "agent_events"

    def _observation_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "observations"

    def _run_deadline_epoch(self, run: RunRecord, frozen: FrozenSpec) -> float:
        return parse_utc_timestamp(run.created_at) + frozen.spec.budget.wall_clock_seconds

    def _run_deadline_reached(self, run: RunRecord, frozen: FrozenSpec) -> bool:
        return time.time() >= self._run_deadline_epoch(run, frozen)

    def _remaining_run_seconds(self, run: RunRecord, frozen: FrozenSpec) -> int:
        return max(0, int(self._run_deadline_epoch(run, frozen) - time.time()))

    def _active_agent_session_count(self, run_id: str) -> int:
        return len(
            [
                session
                for session in self._load_agent_sessions(run_id)
                if session.status not in TERMINAL_AGENT_SESSION_STATUSES
                and session.status != AgentSessionStatus.QUEUED.value
            ]
        )

    def _agent_session_is_stale(self, session: AgentSessionRecord) -> bool:
        if session.status in TERMINAL_AGENT_SESSION_STATUSES:
            return False
        return time.time() - parse_utc_timestamp(session.last_heartbeat_at) > session.budget.stale_after_seconds

    def _refresh_agent_session_deadlines(self, run_id: str) -> None:
        now = time.time()
        for session in self._load_agent_sessions(run_id):
            if session.status in TERMINAL_AGENT_SESSION_STATUSES:
                continue
            if now < parse_utc_timestamp(session.budget.deadline_at):
                continue
            updated = session.model_copy(
                update={
                    "status": AgentSessionStatus.TIMED_OUT.value,
                    "phase": AgentSessionPhase.IDLE.value,
                    "updated_at": utc_timestamp(),
                    "summary": "agent session exceeded its hard deadline",
                }
            )
            self._write_agent_session(updated)
            self._append_agent_event(
                run_id,
                "agent_timed_out",
                session.agent_session_id,
                {"deadline_at": session.budget.deadline_at},
            )

    def _enforce_run_deadline(self, run_id: str) -> bool:
        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        if not self._run_deadline_reached(run, frozen):
            return False
        active_count = self._active_agent_session_count(run_id)
        if active_count > 0:
            self.abort_all_agent_sessions(run_id, "run budget exhausted")
        self._append_agent_event(
            run_id,
            "run_deadline",
            None,
            {"run_deadline_at": utc_timestamp_from_epoch(self._run_deadline_epoch(run, frozen))},
            dedupe_type=True,
        )
        return True

    def _append_agent_event(
        self,
        run_id: str,
        event_type: str,
        agent_session_id: str | None,
        payload: dict[str, Any] | None = None,
        *,
        dedupe_type: bool = False,
    ) -> AgentSessionEvent:
        if dedupe_type:
            existing = [
                event
                for event in self._load_agent_events(run_id)
                if event.type == event_type and event.agent_session_id == agent_session_id
            ]
            if existing:
                return existing[-1]
        run = self._load_run(run_id)
        event = AgentSessionEvent(
            event_id=f"event_{run.next_agent_event_index:06d}",
            run_id=run_id,
            agent_session_id=agent_session_id,
            type=event_type,
            created_at=utc_timestamp(),
            payload=payload or {},
        )
        run.next_agent_event_index += 1
        self._write_run(run)
        self._write_agent_event(event)
        return event

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

    def _write_agent_event(self, event: AgentSessionEvent) -> None:
        write_json(
            self._agent_event_dir(event.run_id) / f"{event.event_id}.json",
            event.model_dump(mode="json"),
        )

    def _load_agent_events(self, run_id: str) -> list[AgentSessionEvent]:
        event_dir = self._agent_event_dir(run_id)
        if not event_dir.exists():
            return []
        return [
            AgentSessionEvent.model_validate(load_json(path))
            for path in sorted(event_dir.glob("event_*.json"))
        ]

    def _write_observation(self, observation: AgentObservation) -> None:
        write_json(
            self._observation_dir(observation.run_id) / f"{observation.observation_id}.json",
            observation.model_dump(mode="json"),
        )

    def _load_observations(self, run_id: str) -> list[AgentObservation]:
        observation_dir = self._observation_dir(run_id)
        if not observation_dir.exists():
            return []
        return [
            AgentObservation.model_validate(load_json(path))
            for path in sorted(observation_dir.glob("obs_*.json"))
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
