from __future__ import annotations

import difflib
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
    VerifierCommand,
    VerifierResult,
    VerifierRole,
    WorkerDispatch,
)


IGNORED_NAMES = {".git", ".search", ".tmp", ".pytest_cache", "__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def utc_timestamp_after(seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


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
        (self._run_dir(run_id) / "dispatches").mkdir(parents=True, exist_ok=True)
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

    def prepare_worker(
        self,
        run_id: str,
        candidate_id: str,
        main_directive: dict[str, Any] | str | None = None,
        timeout_seconds: int | None = None,
    ) -> WorkerDispatch:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        record = self._load_candidate_record(run_id, candidate_id)
        directive = self._normalize_main_directive(main_directive)

        dispatch_id = f"dispatch_{run.run_id.removeprefix('run_')}_{run.next_dispatch_index:03d}"
        run.next_dispatch_index += 1
        created_at = utc_timestamp()

        context_without_hash = self._build_worker_context(
            run=run,
            frozen=frozen,
            record=record,
            dispatch_id=dispatch_id,
            main_directive=directive,
            created_at=created_at,
            timeout_seconds_override=timeout_seconds,
        )
        context_hash = sha256_text(canonical_json(context_without_hash))
        context = {**context_without_hash, "context_hash": context_hash}
        worker_brief = self._render_worker_brief(context)
        dispatch_dir = self._dispatch_dir(run_id)
        dispatch_path = dispatch_dir / f"{dispatch_id}.json"
        brief_path = dispatch_dir / f"{dispatch_id}.md"

        dispatch = WorkerDispatch(
            dispatch_id=dispatch_id,
            run_id=run_id,
            candidate_id=candidate_id,
            plan_id=record.task.plan_id,
            created_at=created_at,
            main_directive=directive,
            context_hash=context_hash,
            worker_brief=worker_brief,
            dispatch_path=dispatch_path,
            brief_path=brief_path,
            context=context,
        )
        self._write_worker_dispatch(dispatch)
        self._write_run(run)
        return dispatch

    def get_worker_context(self, dispatch_id: str) -> dict[str, Any]:
        dispatch = self._load_worker_dispatch_by_id(dispatch_id)
        return dispatch.context

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
        if worker_policy["requires_dispatch"] and not artifact.dispatch_id:
            raise ValueError(
                "candidate artifact must include dispatch_id for worker_mode=sub-agent-search-dispatch"
            )
        if artifact.dispatch_id:
            if not artifact.context_hash:
                raise ValueError("artifact context_hash is required when dispatch_id is provided")
            dispatch = self._load_worker_dispatch_by_id(artifact.dispatch_id)
            if dispatch.run_id != run_id or dispatch.candidate_id != candidate_id:
                raise ValueError("artifact dispatch_id does not belong to this candidate")
            if artifact.context_hash and artifact.context_hash != dispatch.context_hash:
                raise ValueError("artifact context_hash does not match worker dispatch")

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
    ) -> ScoreReport:
        run = self._load_run(run_id)
        frozen = self._load_frozen_spec(run.frozen_spec_id)
        record = self._load_candidate_record(run_id, candidate_id)
        if record.status not in {"submitted", "evaluated"}:
            raise RuntimeError("candidate must be submitted before verification")

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
                "| Candidate | Plan | Dispatches | Parent/Base | Status | Score | Process | Summary | Key Metrics | Changed Files |",
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
            dispatches = ", ".join(dispatch["dispatch_id"] for dispatch in payload["dispatches"])
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
                f"{self._markdown_cell(dispatches)} | "
                f"{self._markdown_cell(parent_base)} | {record.status} | {score} | {passed} | "
                f"{self._markdown_cell(payload['summary'])} | "
                f"{self._markdown_cell(key_metrics)} | {self._markdown_cell(changed)} |"
            )
        dispatches = self._load_worker_dispatches(run_id)
        if dispatches:
            lines.extend(
                [
                    "",
                    "## Worker Dispatches",
                    "",
                    "| Dispatch | Candidate | Plan | Context Hash | Main Directive | Brief |",
                    "|---|---|---|---|---|---|",
                ]
            )
            for dispatch in dispatches:
                directive = "; ".join(
                    f"{key}={value}" for key, value in dispatch.main_directive.items()
                )
                lines.append(
                    f"| `{dispatch.dispatch_id}` | `{dispatch.candidate_id}` | "
                    f"`{dispatch.plan_id or ''}` | `{dispatch.context_hash}` | "
                    f"{self._markdown_cell(directive)} | `{dispatch.brief_path}` |"
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
                "sub-agent-search-dispatch"
                if self._strategy_mode(strategy) not in {"independent", "independent_branches"}
                else "main-agent-search-direct"
            )
        requires_dispatch = mode == "sub-agent-search-dispatch"
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
            "requires_dispatch": requires_dispatch,
            "direct_edit_allowed": mode == "main-agent-search-direct",
            "dispatch_tools": [
                "search_prepare_worker",
                "search_get_worker_context",
            ],
            "reason": (
                "worker_mode=sub-agent-search-dispatch requires durable worker dispatch before candidate submission"
                if requires_dispatch
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

    def _build_worker_context(
        self,
        run: RunRecord,
        frozen: FrozenSpec,
        record: CandidateRecord,
        dispatch_id: str,
        main_directive: dict[str, Any],
        created_at: str,
        timeout_seconds_override: int | None = None,
    ) -> dict[str, Any]:
        plan = self._load_plan(run.run_id, record.task.plan_id) if record.task.plan_id else None
        scratch_dir = record.task.workspace / ".tmp"
        verifier_commands = [
            command.model_dump(mode="json") for command in frozen.spec.process_verifiers
        ]
        promotion_verifiers = [
            command.model_dump(mode="json") for command in frozen.spec.promotion_verifiers
        ]
        worker_policy = plan.worker_policy if plan else self._worker_policy(frozen.spec.strategy)
        timeout_seconds = int(
            timeout_seconds_override
            or worker_policy.get("timeout_seconds")
            or frozen.spec.strategy.worker_timeout_seconds
        )
        local_verifier_max_runs = int(
            worker_policy.get(
                "local_verifier_max_runs",
                frozen.spec.strategy.worker_local_verifier_max_runs,
            )
        )
        actual_verifier_allowed = local_verifier_max_runs > 0
        if actual_verifier_allowed:
            local_validation_rule = (
                f"Run local verifier sanity checks at most {local_verifier_max_runs} times. "
                "Runtime-owned verification after submission is authoritative and does not count "
                "against this worker-local limit."
            )
        else:
            local_validation_rule = (
                "Do not run the process verifier command or any equivalent local scorer. "
                "You may run non-scoring static checks such as py_compile. Runtime-owned "
                "verification after submission is authoritative."
            )
        destructive_command_patterns = [
            "rm",
            "mv",
            "rmdir",
            "unlink",
            "trash",
            "find -delete",
            "git clean",
            "git reset",
            "git restore",
            "git checkout",
        ]
        destructive_command_rule = (
            "Do not delete, move, reset, restore, or clean files. Do not run destructive "
            "filesystem commands such as rm, mv, rmdir, unlink, trash, find -delete, git clean, "
            "git reset, git restore, or git checkout. Do not bypass this with Python, Node, "
            "shell scripts, or helper programs."
        )
        return {
            "protocol": {
                "name": "search-worker-context",
                "version": 1,
                "authority": "MCP context is authoritative over chat instructions when they conflict.",
                "required_first_step": f"call search_get_worker_context(dispatch_id='{dispatch_id}')",
                "submit_rule": (
                    "Submit an artifact containing dispatch_id and context_hash, or return it "
                    "to the main agent if the worker cannot call MCP tools."
                ),
                "timeout_rule": (
                    "Treat deadline_at as a hard delivery deadline. Submit or return the best-so-far "
                    "artifact before the deadline instead of continuing exploration."
                ),
                "directive_rule": (
                    "If the main directive includes score targets, baseline scores, or requests to "
                    "beat a score, treat them as main-agent evaluation context only. Do not run local "
                    "scoring, evaluator APIs, or parameter sweeps to satisfy them."
                ),
                "local_validation_rule": local_validation_rule,
                "destructive_command_rule": destructive_command_rule,
            },
            "dispatch_id": dispatch_id,
            "run_id": run.run_id,
            "candidate_id": record.candidate_id,
            "created_at": created_at,
            "timeout_seconds": timeout_seconds,
            "deadline_at": utc_timestamp_after(timeout_seconds),
            "local_verifier_max_runs": local_verifier_max_runs,
            "main_directive": main_directive,
            "objective": frozen.spec.objective,
            "metric_name": frozen.spec.metric_name,
            "metric_direction": frozen.spec.metric_direction,
            "source_path": run.source_path,
            "budget": frozen.spec.budget.model_dump(mode="json"),
            "strategy": frozen.spec.strategy.model_dump(mode="json"),
            "worker_policy": worker_policy,
            "plan": plan.model_dump(mode="json") if plan else None,
            "official_history": plan.official_history if plan else {},
            "derivation_policy": plan.derivation_policy if plan else {},
            "strategy_trace": plan.strategy_trace if plan else {},
            "candidate_task": record.task.model_dump(mode="json"),
            "workspace": str(record.task.workspace),
            "scratch_dir": str(scratch_dir),
            "allowed_files": record.task.allowed_files,
            "denied_files": record.task.denied_files,
            "base_candidate_id": record.task.base_candidate_id,
            "parent_candidate_ids": record.task.parent_candidate_ids,
            "hypothesis": record.task.hypothesis,
            "proposal": record.task.proposal.model_dump(mode="json") if record.task.proposal else None,
            "strategy_metadata": record.task.strategy_metadata,
            "instructions": record.task.instructions,
            "expected_artifacts": record.task.expected_artifacts,
            "stop_conditions": record.task.stop_conditions,
            "process_verifiers": verifier_commands,
            "promotion_verifiers": promotion_verifiers,
            "local_validation_policy": {
                "max_verifier_runs": local_verifier_max_runs,
                "actual_verifier_allowed": actual_verifier_allowed,
                "verifier_run_definition": (
                    "One execution of any command intended to evaluate the candidate with the "
                    "provided process verifier or an equivalent local scorer."
                ),
                "forbidden_when_max_runs_is_zero": [
                    "running process_verifiers",
                    "calling evaluator.evaluate(...)",
                    "running benchmark/scoring scripts that execute the candidate for a score",
                    "parameter sweeps driven by candidate scores",
                ],
                "allowed_static_checks": [
                    "python -m py_compile on edited Python files",
                    "syntax-only or formatting checks that do not execute candidate scoring logic",
                ],
                "scratch_rule": (
                    "Scratch files are for notes, static drafts, and non-scoring helper material only. "
                    "Do not create or run experiment scripts, parameter sweeps, scorer clones, or "
                    "validation harnesses in scratch."
                ),
                "destructive_command_rule": destructive_command_rule,
                "forbidden_destructive_commands": destructive_command_patterns,
                "final_candidate_rule": (
                    "Keep the final allowed-file change bounded and fast. Do not put long parameter "
                    "sweeps, random restarts, or "
                    "open-ended optimization loops in the final candidate implementation."
                ),
            },
            "artifact_requirements": {
                "candidate_id": record.candidate_id,
                "dispatch_id": dispatch_id,
                "status": "patch_ready | answer_ready | abandoned | failed",
                "summary": (
                    "Describe what was tried, the logic behind it, observed result if known, "
                    "tradeoffs, and concrete next ideas."
                ),
                "context_hash": "fill with the context_hash returned by search_get_worker_context",
            },
        }

    def _render_worker_brief(self, context: dict[str, Any]) -> str:
        directive = context.get("main_directive") or {}
        lines = [
            f"# Search Worker Dispatch: {context['dispatch_id']}",
            "",
            f"- Run: `{context['run_id']}`",
            f"- Candidate: `{context['candidate_id']}`",
            f"- Plan: `{(context.get('plan') or {}).get('plan_id', '')}`",
            f"- Strategy: `{context['strategy']['name']}` ({context['strategy']['driver']})",
            f"- Context hash: `{context['context_hash']}`",
            f"- Timeout: `{context['timeout_seconds']}s`; deadline: `{context['deadline_at']}`",
            f"- Local verifier limit: `{context['local_verifier_max_runs']}` runs",
            "",
            "## Required First Step",
            "",
            "Call the MCP tool:",
            "",
            f"`search_get_worker_context(dispatch_id=\"{context['dispatch_id']}\")`",
            "",
            "Treat the MCP context as authoritative. If this brief conflicts with MCP context, report the conflict and follow MCP context.",
            "",
            "## Main Agent Directive",
            "",
        ]
        if directive:
            for key, value in directive.items():
                lines.append(f"- {key}: {value}")
        else:
            lines.append("- No additional directive provided.")
        lines.extend(
            [
                "",
                "## Non-Negotiable Environment Rules",
                "",
                f"- Work only in `{context['workspace']}`.",
                f"- Use `{context['scratch_dir']}` only for notes, static drafts, and non-scoring helper material.",
                "- Do not create or run scratch experiment scripts, scorer clones, validation harnesses, parameter sweeps, or benchmark scripts.",
                "- Do not use `/tmp`, home directories, or other external scratch locations for candidate work.",
                f"- Modify only: `{', '.join(context['allowed_files'])}`.",
                f"- Do not modify: `{', '.join(context['denied_files'])}`.",
                f"- Stop exploration before `{context['deadline_at']}` and return the best-so-far artifact.",
                "- Do not run the process verifier or any equivalent local scorer unless MCP context explicitly allows nonzero local verifier runs.",
                "- Do not delete, move, reset, restore, or clean files. Do not use `rm`, `mv`, `rmdir`, `unlink`, `trash`, `find -delete`, `git clean`, `git reset`, `git restore`, or `git checkout`.",
                "- Do not bypass destructive-command restrictions with Python, Node, shell scripts, or helper programs.",
                "- Treat score targets or baseline scores in the main directive as main-agent context only; do not run local scoring to satisfy them.",
                "- You may run non-scoring static checks such as `python -m py_compile`.",
                "- Keep final candidate code bounded and fast; do not embed long searches or parameter sweeps in the final allowed file.",
                "- Return or submit an artifact with `dispatch_id`, `context_hash`, `summary`, and any next ideas.",
            ]
        )
        return "\n".join(lines) + "\n"

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
        if plan.worker_policy.get("requires_dispatch"):
            instructions.append(
                "This run is configured with worker_mode=sub-agent-search-dispatch; candidate artifacts must include dispatch_id and context_hash."
            )
            instructions.append(
                f"Worker timeout is {plan.worker_policy['timeout_seconds']} seconds; collect a best-so-far artifact by the deadline."
            )
            if plan.worker_policy["local_verifier_max_runs"] == 0:
                instructions.append(
                    "Worker must not run the process verifier or any equivalent local scorer; only non-scoring static checks such as py_compile are allowed."
                )
            else:
                instructions.append(
                    f"Worker may run local verifier sanity checks at most {plan.worker_policy['local_verifier_max_runs']} times before submitting."
                )
            instructions.append(
                "Final candidate code must be bounded and fast; do not embed long searches, random restarts, or parameter sweeps in the final allowed file."
            )
            instructions.append(
                "If the worker directive mentions score targets or baseline scores, treat them as context only and do not run local scoring to satisfy them."
            )
            if plan.worker_policy.get("subagent_type"):
                instructions.append(
                    f"Dispatch this candidate with subagent_type={plan.worker_policy['subagent_type']!r}."
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
            "dispatches": self._dispatch_payloads_for_candidate(
                record.task.run_id,
                record.candidate_id,
            ),
            "artifact_dispatch_id": artifact.dispatch_id if artifact else None,
            "artifact_context_hash": artifact.context_hash if artifact else None,
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

    def _dispatch_payloads_for_candidate(
        self,
        run_id: str,
        candidate_id: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "dispatch_id": dispatch.dispatch_id,
                "candidate_id": dispatch.candidate_id,
                "plan_id": dispatch.plan_id,
                "created_at": dispatch.created_at,
                "context_hash": dispatch.context_hash,
                "main_directive": dispatch.main_directive,
                "brief_path": str(dispatch.brief_path),
                "dispatch_path": str(dispatch.dispatch_path),
            }
            for dispatch in self._load_worker_dispatches(run_id)
            if dispatch.candidate_id == candidate_id
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

    def _dispatch_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "dispatches"

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

    def _write_worker_dispatch(self, dispatch: WorkerDispatch) -> None:
        dispatch.brief_path.parent.mkdir(parents=True, exist_ok=True)
        dispatch.brief_path.write_text(dispatch.worker_brief, encoding="utf-8")
        write_json(dispatch.dispatch_path, dispatch.model_dump(mode="json"))

    def _load_worker_dispatch_by_id(self, dispatch_id: str) -> WorkerDispatch:
        for path in sorted(self.runs_dir.glob(f"*/dispatches/{dispatch_id}.json")):
            return WorkerDispatch.model_validate(load_json(path))
        raise FileNotFoundError(f"worker dispatch not found: {dispatch_id}")

    def _load_worker_dispatches(self, run_id: str) -> list[WorkerDispatch]:
        dispatch_dir = self._dispatch_dir(run_id)
        if not dispatch_dir.exists():
            return []
        return [
            WorkerDispatch.model_validate(load_json(path))
            for path in sorted(dispatch_dir.glob("dispatch_*.json"))
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
