from __future__ import annotations

import difflib
import hashlib
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
    CandidateTask,
    FrozenSpec,
    RunRecord,
    RunState,
    RunSummary,
    ScoreReport,
    SearchSpec,
    VerifierCommand,
    VerifierResult,
    VerifierRole,
)


IGNORED_NAMES = {".git", ".search", ".pytest_cache", "__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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

    def next_batch(self, run_id: str, k: int) -> list[CandidateTask]:
        if k <= 0:
            raise ValueError("k must be > 0")

        run = self._load_run(run_id)
        if run.state not in {RunState.RUNNING, RunState.WAITING_FOR_WORKERS, RunState.SELECTING}:
            raise RuntimeError(f"cannot create candidates from state {run.state}")

        frozen = self._load_frozen_spec(run.frozen_spec_id)
        spec = frozen.spec
        source = Path(run.source_path)
        remaining = spec.budget.max_candidates - run.candidates_total
        count = min(k, remaining)
        tasks: list[CandidateTask] = []

        for _ in range(count):
            candidate_id = f"c{run.next_candidate_index:03d}"
            workspace = self._run_dir(run_id) / "workspace" / candidate_id
            copy_source_tree(source, workspace)
            hypothesis_index = run.next_candidate_index - 1
            if hypothesis_index < len(spec.root_hypotheses):
                hypothesis = spec.root_hypotheses[hypothesis_index]
            else:
                hypothesis = f"Independent candidate {candidate_id}"

            task = CandidateTask(
                run_id=run.run_id,
                candidate_id=candidate_id,
                parent_id=None,
                hypothesis=hypothesis,
                workspace=workspace,
                allowed_files=spec.edit_surface.allow,
                denied_files=spec.edit_surface.deny,
                instructions=[
                    "Work only inside this candidate workspace.",
                    "Modify only allowed files.",
                    "Do not modify frozen verifier files.",
                    "Submit artifacts to the runtime; do not change the main workspace.",
                ],
                expected_artifacts=["patch", "notes", "logs"],
                stop_conditions={
                    "max_worker_seconds": spec.budget.max_worker_seconds,
                },
            )
            record = CandidateRecord(candidate_id=candidate_id, status="created", task=task)
            self._write_candidate_record(run_id, record)
            tasks.append(task)
            run.next_candidate_index += 1
            run.candidates_total += 1

        if tasks:
            run.state = RunState.WAITING_FOR_WORKERS
            self._write_run(run)
        return tasks

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
        if strategy != "independent_branches":
            raise ValueError("only independent_branches is implemented in V0")

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
        report_path = self._run_dir(run_id) / "report.md"

        lines = [
            f"# Search Report: {run_id}",
            "",
            f"- Frozen spec: `{frozen.frozen_spec_id}`",
            f"- Spec hash: `{frozen.spec_hash}`",
            f"- Objective: {frozen.spec.objective}",
            f"- Metric: `{frozen.spec.metric_name}` ({frozen.spec.metric_direction})",
            f"- Best candidate: `{run.best_candidate_id}`",
            f"- Best score: `{run.best_score}`",
            "",
            "## Candidates",
            "",
            "| Candidate | Status | Score | Process | Denied Files | Changed Files |",
            "|---|---|---:|---|---|---|",
        ]
        for record in records:
            score = ""
            passed = ""
            if record.score_report:
                score = "" if record.score_report.aggregate_score is None else str(record.score_report.aggregate_score)
                passed = str(record.score_report.process_passed)
            changed = ", ".join(record.detected_changed_files)
            lines.append(
                f"| `{record.candidate_id}` | {record.status} | {score} | {passed} | "
                f"{record.touched_denied_files} | {changed} |"
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

    def _load_frozen_spec(self, frozen_spec_id: str) -> FrozenSpec:
        return FrozenSpec.model_validate(load_json(self._spec_dir(frozen_spec_id) / "frozen_spec.json"))

    def _load_run(self, run_id: str) -> RunRecord:
        return RunRecord.model_validate(load_json(self._run_dir(run_id) / "run.json"))

    def _write_run(self, run: RunRecord) -> None:
        write_json(self._run_dir(run.run_id) / "run.json", run.model_dump(mode="json"))

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

