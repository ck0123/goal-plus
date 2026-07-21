from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading

import pytest

from goal_plus.runtime import FileSearchRuntime
from goal_plus.space_agent import (
    DEFAULT_SCHEMA_CONSOLIDATION_INTERVAL,
    FileSearchSpaceRuntime,
    InterventionPlanProposal,
    ReviewerExecution,
    SchemaReviewerExecution,
    SpaceCoverageEntry,
    SpaceOverlap,
    SpaceRealizedEvidence,
    SpaceReviewDecision,
    SpaceSchemaUpdate,
    candidate_pre_tool_block_reason,
)
from tests._runtime_helpers import make_project, spec_with_host


SPACE_VIEW_NAMES = (
    "artifact",
    "configuration",
    "mechanism",
    "context",
    "epistemic",
    "behavior",
)


def schema() -> dict:
    return {
        "schema_version": "test-v1",
        "views": {
            name: {"description": f"test {name}"} for name in SPACE_VIEW_NAMES
        },
    }


def proposal(name: str = "first") -> InterventionPlanProposal:
    return InterventionPlanProposal(
        intervention=f"change {name}",
        scope=f"target {name} under the public workload",
        expected_new_information=f"whether {name} changes verifier behavior",
    )


def setup_runtime(tmp_path: Path) -> tuple[FileSearchRuntime, str, str, str, Path]:
    project = make_project(tmp_path)
    (project / "space-schema.json").write_text(
        json.dumps(schema()),
        encoding="utf-8",
    )
    runtime = FileSearchRuntime(tmp_path / ".gp")
    frozen = runtime.freeze_spec(
        spec_with_host(project, "codex", strategy_name="random", max_candidates=1),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    task = runtime.start_batch(run_id, plan.plan_id)[0]
    session = runtime.start_agent_session(run_id, task.candidate_id)
    return runtime, run_id, task.candidate_id, session.agent_session_id, task.workspace


def setup_parallel_runtime(
    tmp_path: Path,
) -> tuple[FileSearchRuntime, str, list[tuple[str, str, Path]]]:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".gp")
    frozen = runtime.freeze_spec(
        spec_with_host(project, "codex", strategy_name="random", max_candidates=2),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    sessions = [
        runtime.start_agent_session(run_id, task.candidate_id) for task in tasks
    ]
    return runtime, run_id, [
        (task.candidate_id, session.agent_session_id, task.workspace)
        for task, session in zip(tasks, sessions, strict=True)
    ]


def open_experiment(runtime: FileSearchRuntime, run_id: str, mode: str) -> None:
    runtime.open_space_experiment(
        run_id,
        mode=mode,  # type: ignore[arg-type]
        schema_path="space-schema.json",
        experiment_id=f"test-{mode}",
        reviewer_model="gpt-5.6-sol",
        reviewer_reasoning_effort="medium",
        reviewer_timeout_seconds=30,
    )


def open_search_space(
    runtime: FileSearchRuntime,
    run_id: str,
    mode: str,
    *,
    schema_consolidation_interval: int = DEFAULT_SCHEMA_CONSOLIDATION_INTERVAL,
) -> None:
    runtime.open_search_space(
        run_id,
        mode=mode,  # type: ignore[arg-type]
        reviewer_model="gpt-5.6-sol",
        reviewer_reasoning_effort="medium",
        reviewer_timeout_seconds=30,
        schema_consolidation_interval=schema_consolidation_interval,
    )


def space_dir(runtime: FileSearchRuntime, run_id: str) -> Path:
    return runtime.root_dir / "runs" / run_id / "search-space"


def persisted_space_plan(
    runtime: FileSearchRuntime,
    run_id: str,
    plan_id: str,
) -> dict:
    path = space_dir(runtime, run_id) / "plans" / f"{plan_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def content_sha256(payload: dict) -> str:
    content = dict(payload)
    content.pop("content_sha256", None)
    encoded = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def accepted_review(
    *,
    region_key: str | None = None,
    point_key: str | None = None,
) -> SpaceReviewDecision:
    return SpaceReviewDecision(
        decision="accept",
        duplicate_of=[],
        reason_code="novel",
        overlap=SpaceOverlap(
            artifact="low",
            configuration="none",
            mechanism="low",
            context="high",
            epistemic="none",
            behavior="low",
        ),
        confidence=0.9,
        rationale="audit-only acceptance rationale",
        region_key=region_key,
        point_key=point_key,
    )


def rejected_review(
    duplicate_of: str,
    *,
    active: bool = False,
    region_key: str | None = None,
    point_key: str | None = None,
) -> SpaceReviewDecision:
    return SpaceReviewDecision(
        decision="reject",
        duplicate_of=[duplicate_of],
        reason_code=("active_plan_collision" if active else "duplicate_prior_intervention"),
        overlap=SpaceOverlap(
            artifact="exact",
            configuration="high",
            mechanism="exact",
            context="exact",
            epistemic="high",
            behavior="high",
        ),
        confidence=0.97,
        rationale="audit-only duplicate rationale",
        region_key=region_key,
        point_key=point_key,
    )


class AcceptThenRejectReviewer:
    def review(self, _config, _proposal, covered):
        decision = (
            accepted_review()
            if not covered
            else rejected_review(covered[-1].plan_id)
        )
        return ReviewerExecution(result=decision, latency_ms=12, usage={"input_tokens": 42})


class BrokenReviewer:
    def review(self, _config, _proposal, _covered):
        raise RuntimeError("synthetic reviewer outage")


class UnknownDuplicateReviewer:
    def review(self, _config, _proposal, _covered):
        return ReviewerExecution(
            result=rejected_review("ip-9999"),
            latency_ms=7,
            usage={"input_tokens": 12},
        )


class ConcurrentCollisionReviewer:
    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)
        self.lock = threading.Lock()
        self.calls = 0

    def review(self, _config, _proposal, covered):
        with self.lock:
            self.calls += 1
            call = self.calls
        if call <= 2:
            self.barrier.wait(timeout=5)
        active = [plan for plan in covered if plan.status in {"accepted", "verifying"}]
        if active:
            decision = rejected_review(
                active[-1].plan_id,
                region_key="loop-schedule",
                point_key="loop-schedule:factor-2",
            )
        elif covered:
            decision = rejected_review(
                covered[-1].plan_id,
                region_key="loop-schedule",
                point_key="loop-schedule:factor-2",
            )
        else:
            decision = accepted_review(
                region_key="loop-schedule",
                point_key="loop-schedule:factor-2",
            )
        return ReviewerExecution(result=decision, latency_ms=5, usage={})


class SamePointReviewer:
    def review(self, _config, _proposal, covered):
        decision = (
            accepted_review(region_key="same-region", point_key="same-point")
            if not covered
            else rejected_review(
                covered[0].plan_id,
                region_key="same-region",
                point_key="same-point",
            )
        )
        return ReviewerExecution(result=decision, latency_ms=4, usage={})


class ConsolidatingReviewer:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls = 0
        self.checkpoint_calls = 0
        self.search_states: list[dict] = []
        self.covered_plan_ids: list[list[str]] = []

    def review(self, config, proposal, covered):
        runtime_state = json.loads(
            json.dumps(config.space_schema["_runtime_search_state"])
        )
        with self.lock:
            self.calls += 1
            self.search_states.append(runtime_state)
            self.covered_plan_ids.append([plan.plan_id for plan in covered])
        return ReviewerExecution(
            result=accepted_review(
                region_key="independent-work",
                point_key=f"point:{proposal.intervention}",
            ),
            latency_ms=3,
            usage={},
        )

    def consolidate(self, config) -> SchemaReviewerExecution:
        runtime_state = json.loads(
            json.dumps(config.space_schema["_runtime_search_state"])
        )
        with self.lock:
            self.checkpoint_calls += 1
        return SchemaReviewerExecution(
            result=self._schema_update(config.space_schema, runtime_state),
            latency_ms=7,
            usage={"input_tokens": 20},
        )

    @staticmethod
    def _schema_update(
        schema_with_runtime: dict,
        runtime_state: dict,
    ) -> SpaceSchemaUpdate:
        next_schema = json.loads(json.dumps(schema_with_runtime))
        next_schema.pop("_runtime_search_state")
        coverage = [
            SpaceCoverageEntry.model_validate(item)
            for item in runtime_state["coverage"]
        ]
        already_covered = {
            event_id
            for entry in coverage
            for event_id in entry.evidence_event_ids
        }
        for event in runtime_state["tail_events"]:
            if not event["coverage_eligible"] or event["event_id"] in already_covered:
                continue
            realized = event["realized_evidence"]
            declared = event["proposal"]
            coverage.append(
                SpaceCoverageEntry(
                    coverage_id=f"coverage:{event['event_id']}",
                    description=(
                        declared.get("intervention")
                        or declared.get("proposed_change")
                        or event["plan_id"]
                    ),
                    context=(
                        declared.get("scope")
                        or declared.get("target")
                        or "unspecified context"
                    ),
                    evidence_event_ids=[event["event_id"]],
                    evidence_plan_ids=[event["plan_id"]],
                    outcomes=[realized["outcome"]],
                )
            )
        tail_event_ids = [
            event["event_id"] for event in runtime_state["tail_events"]
        ]
        return SpaceSchemaUpdate(
            space_schema=next_schema,
            coverage=coverage,
            revision_summary=(
                "Consolidated immutable verifier evidence through "
                f"{runtime_state['target_event_id']}."
            ),
            revision_evidence_event_ids=tail_event_ids,
        )


class OmittingConsolidatingReviewer(ConsolidatingReviewer):
    def consolidate(self, config) -> SchemaReviewerExecution:
        execution = super().consolidate(config)
        runtime_state = config.space_schema["_runtime_search_state"]
        malformed_schema = json.loads(json.dumps(execution.result.space_schema))
        malformed_schema["views"].pop("artifact")
        malformed_schema["views"]["suggested_direction"] = {
            "description": "must not become a persisted view"
        }
        return SchemaReviewerExecution(
            result=execution.result.model_copy(
                update={
                    "space_schema": malformed_schema,
                    "coverage": execution.result.coverage[:1],
                    "revision_evidence_event_ids": [
                        runtime_state["tail_events"][-1]["event_id"]
                    ],
                }
            ),
            latency_ms=execution.latency_ms,
            usage=execution.usage,
        )


class BrokenConsolidatingReviewer(ConsolidatingReviewer):
    def consolidate(self, _config) -> SchemaReviewerExecution:
        with self.lock:
            self.checkpoint_calls += 1
        raise RuntimeError("synthetic schema reviewer failure")


class BlockingConsolidatingReviewer(ConsolidatingReviewer):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def consolidate(self, config) -> SchemaReviewerExecution:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("timed out waiting to release schema reviewer")
        return super().consolidate(config)


class SchemaDuplicateReviewer(ConsolidatingReviewer):
    def review(self, config, proposal, covered):
        runtime_state = config.space_schema["_runtime_search_state"]
        if runtime_state["coverage"] and not runtime_state["schema_refresh_due"]:
            with self.lock:
                self.calls += 1
                self.search_states.append(json.loads(json.dumps(runtime_state)))
                self.covered_plan_ids.append([plan.plan_id for plan in covered])
            duplicate_of = runtime_state["coverage"][0]["evidence_plan_ids"][0]
            return ReviewerExecution(
                result=rejected_review(duplicate_of),
                latency_ms=3,
                usage={},
            )
        return super().review(config, proposal, covered)


def complete_space_plan(
    runtime: FileSearchRuntime,
    *,
    run_id: str,
    candidate_id: str,
    session_id: str,
    plan_id: str,
    iteration: int,
    outcome: str = "neutral",
) -> None:
    runtime.search_space.begin_verifier(
        run_id=run_id,
        candidate_id=candidate_id,
        agent_session_id=session_id,
        plan_id=plan_id,
    )
    infrastructure_failure = outcome == "infrastructure_failure"
    process_passed = outcome in {"improved", "neutral", "regressed"}
    runtime.search_space.complete_verifier(
        run_id=run_id,
        plan_id=plan_id,
        iteration=iteration,
        score=0.0 if process_passed else None,
        process_passed=process_passed,
        git_head=f"head-{iteration}",
        artifact_hash=f"artifact-{iteration}",
        failure_class="VerifierStartFailed" if infrastructure_failure else None,
        verifier_metrics={},
        realized_evidence=SpaceRealizedEvidence(
            base_git_head=f"base-{iteration}",
            result_git_head=f"head-{iteration}",
            artifact_hash=f"artifact-{iteration}",
            artifact_delta_sha256=f"delta-{iteration}",
            changed_files=["initial_program.py"],
            delta_files=["initial_program.py"],
            changed_symbols=["VALUE"],
            diff_stat="1 file changed",
            diff_patch=f"-VALUE = {iteration - 1}\n+VALUE = {iteration}\n",
            metric_name="combined_score",
            metric_direction="maximize",
            score_before=0.0,
            score_after=0.0 if process_passed else None,
            score_delta=0.0 if process_passed else None,
            outcome=outcome,  # type: ignore[arg-type]
            validity_passed=process_passed,
            process_passed=process_passed,
            infrastructure_failure=infrastructure_failure,
            failure_class=(
                "VerifierStartFailed" if infrastructure_failure else "invalid edit"
                if outcome == "invalid"
                else None
            ),
            completed_at=f"2026-07-20T00:00:{iteration:02d}Z",
        ),
    )


def build_second_schema_snapshot(
    tmp_path: Path,
) -> tuple[FileSearchRuntime, str, str, str, ConsolidatingReviewer, Path]:
    runtime, run_id, candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    reviewer = ConsolidatingReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(
        runtime,
        run_id,
        "enforce",
        schema_consolidation_interval=2,
    )
    for iteration in (1, 2):
        admission = runtime.propose_search_space_plan(
            session_id,
            proposal(f"seed-{iteration}"),
        )
        complete_space_plan(
            runtime,
            run_id=run_id,
            candidate_id=candidate_id,
            session_id=session_id,
            plan_id=admission["plan_id"],
            iteration=iteration,
        )
    runtime.propose_search_space_plan(session_id, proposal("checkpoint"))
    snapshot_path = space_dir(runtime, run_id) / "schemas" / "schema-000002.json"
    return runtime, run_id, candidate_id, session_id, reviewer, snapshot_path


def test_b1_requires_one_plan_per_candidate_verifier(tmp_path: Path) -> None:
    runtime, run_id, candidate_id, session_id, workspace = setup_runtime(tmp_path)
    open_experiment(runtime, run_id, "b1")

    context = runtime.get_agent_context(session_id)
    assert context["search_space"]["enabled"] is True
    assert context["search_space"]["outstanding_plan_id"] is None

    admission = runtime.propose_intervention(session_id, proposal())
    assert admission == {"decision": "accept", "plan_id": "ip-0001"}
    plan_id = admission["plan_id"]

    with pytest.raises(RuntimeError, match="outstanding intervention plan"):
        runtime.propose_intervention(session_id, proposal("second"))
    with pytest.raises(PermissionError, match="require intervention_plan_id"):
        runtime.run_verifier(run_id, candidate_id, agent_session_id=session_id)

    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    report = runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        intervention_plan_id=plan_id,
        hypothesis="test first plan",
    )
    assert report.process_passed is True
    [iteration] = runtime.list_iterations(run_id, candidate_id)
    assert iteration["intervention_plan_id"] == plan_id
    status = runtime.space_experiment_status(run_id)
    assert status["plan_counts"] == {"completed": 1}

    next_admission = runtime.propose_intervention(session_id, proposal("second"))
    assert next_admission["decision"] == "accept"


def test_search_closeout_aborts_unverified_reservations(tmp_path: Path) -> None:
    runtime, run_id, _candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    runtime.search_space.reviewer = ConsolidatingReviewer()  # type: ignore[assignment]
    open_search_space(runtime, run_id, "enforce")
    admission = runtime.propose_search_space_plan(
        session_id,
        proposal("handoff-only"),
    )

    aborted = runtime.search_space.abort_outstanding(
        run_id,
        reason="test worker drain",
    )

    assert aborted == [admission["plan_id"]]
    status = runtime.search_space_status(run_id)
    assert status["plan_counts"] == {"aborted": 1}
    assert status["active_reservations"] == []
    persisted = persisted_space_plan(runtime, run_id, admission["plan_id"])
    assert persisted["abort_reason"] == "test worker drain"
    assert runtime.propose_search_space_plan(
        session_id,
        proposal("after-closeout"),
    )["decision"] == "accept"


def test_b4_rejection_is_a_pure_non_executable_decision(tmp_path: Path) -> None:
    runtime, run_id, candidate_id, session_id, workspace = setup_runtime(tmp_path)
    runtime.space_experiments.reviewer = AcceptThenRejectReviewer()  # type: ignore[assignment]
    open_experiment(runtime, run_id, "b4")

    first = runtime.propose_intervention(session_id, proposal("covered"))
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        intervention_plan_id=first["plan_id"],
    )

    duplicate = runtime.propose_intervention(session_id, proposal("duplicate"))
    assert duplicate == {
        "plan_id": "ip-0002",
        "decision": "reject",
        "duplicate_of": ["ip-0001"],
        "duplicate_plans": [
            {
                "plan_id": "ip-0001",
                "coverage_status": "completed_coverage",
                "plan_card": {
                    "intervention": "change covered",
                    "scope": "target covered under the public workload",
                    "expected_new_information": (
                        "whether covered changes verifier behavior"
                    ),
                },
            }
        ],
    }
    assert "rationale" not in duplicate
    assert "overlap" not in duplicate
    with pytest.raises(RuntimeError, match="is rejected, expected accepted"):
        runtime.run_verifier(
            run_id,
            candidate_id,
            agent_session_id=session_id,
            intervention_plan_id=duplicate["plan_id"],
        )
    assert len(runtime.list_iterations(run_id, candidate_id)) == 1


def test_b4_reviewer_failure_accepts_but_records_fail_open(tmp_path: Path) -> None:
    runtime, run_id, _candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    runtime.space_experiments.reviewer = BrokenReviewer()  # type: ignore[assignment]
    open_experiment(runtime, run_id, "b4")

    admission = runtime.propose_intervention(session_id, proposal())

    assert admission["decision"] == "accept"
    assert "reviewer" not in admission
    status = runtime.space_experiment_status(run_id)
    assert status["reviewer_fail_open"] == 1
    plan_path = (
        runtime.root_dir
        / "runs"
        / run_id
        / "space-experiment"
        / "plans"
        / "ip-0001.json"
    )
    persisted = json.loads(plan_path.read_text(encoding="utf-8"))
    assert persisted["admission_source"] == "reviewer_fail_open"
    assert "synthetic reviewer outage" in persisted["reviewer_error"]


def test_b4_unknown_duplicate_reference_fails_open(tmp_path: Path) -> None:
    runtime, run_id, _candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    runtime.space_experiments.reviewer = UnknownDuplicateReviewer()  # type: ignore[assignment]
    open_experiment(runtime, run_id, "b4")

    admission = runtime.propose_intervention(session_id, proposal())

    assert admission["decision"] == "accept"
    plan_path = (
        runtime.root_dir
        / "runs"
        / run_id
        / "space-experiment"
        / "plans"
        / "ip-0001.json"
    )
    persisted = json.loads(plan_path.read_text(encoding="utf-8"))
    assert persisted["admission_source"] == "reviewer_fail_open"
    assert "unknown covered intervention plan ids: ip-9999" in persisted[
        "reviewer_error"
    ]


def test_candidate_hook_gate_blocks_mutation_until_acceptance(tmp_path: Path) -> None:
    runtime, run_id, _candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    open_experiment(runtime, run_id, "b1")

    reason = candidate_pre_tool_block_reason(
        runtime.root_dir,
        session_id,
        "functions.exec",
        "const r = await tools.apply_patch('*** Begin Patch'); text(r);",
    )
    assert reason is not None
    assert "accepted intervention plan" in reason
    assert candidate_pre_tool_block_reason(
        runtime.root_dir,
        session_id,
        "functions.exec",
        "const r = await tools.exec_command({cmd:'sed -n 1,20p solution.py'}); text(r);",
    ) is None
    assert candidate_pre_tool_block_reason(
        runtime.root_dir,
        session_id,
        "functions.exec",
        "const r = await tools.exec_command({cmd:'printf x > solution.py'}); text(r);",
    ) is not None
    handoff_patch = """const r = await tools.apply_patch(`*** Begin Patch
*** Add File: /workspace/.tmp/handoff.json
+{}
*** End Patch`); text(r);"""
    assert candidate_pre_tool_block_reason(
        runtime.root_dir,
        session_id,
        "functions.exec",
        handoff_patch,
    ) is None
    mixed_patch = handoff_patch.replace(
        "*** End Patch",
        "*** Update File: /workspace/solution.py\n@@\n-old\n+new\n*** End Patch",
    )
    assert candidate_pre_tool_block_reason(
        runtime.root_dir,
        session_id,
        "functions.exec",
        mixed_patch,
    ) is not None

    combined = (
        "const p = await tools.mcp__goal_plus__search_space_propose({}); "
        "const r = await tools.apply_patch('*** Begin Patch'); text(r);"
    )
    reason = candidate_pre_tool_block_reason(
        runtime.root_dir,
        session_id,
        "functions.exec",
        combined,
    )
    assert reason is not None
    assert "separate tool call" in reason

    runtime.propose_intervention(session_id, proposal())
    assert candidate_pre_tool_block_reason(
        runtime.root_dir,
        session_id,
        "functions.exec",
        "const r = await tools.apply_patch('*** Begin Patch'); text(r);",
    ) is None
    assert candidate_pre_tool_block_reason(
        runtime.root_dir,
        session_id,
        "functions.exec",
        combined,
    ) is not None


def test_candidate_hook_allows_only_verifier_backed_solution_restore(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidate_id, session_id, workspace = setup_runtime(tmp_path)
    open_experiment(runtime, run_id, "b1")
    admission = runtime.propose_intervention(session_id, proposal("verified"))
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        intervention_plan_id=admission["plan_id"],
    )
    [iteration] = runtime.list_iterations(run_id, candidate_id)
    verified_head = iteration["git_head"]
    assert isinstance(verified_head, str)

    assert candidate_pre_tool_block_reason(
        runtime.root_dir,
        session_id,
        "functions.exec",
        f"git restore --source={verified_head[:12]} -- solution.py",
    ) is None
    assert candidate_pre_tool_block_reason(
        runtime.root_dir,
        session_id,
        "functions.exec",
        "git checkout deadbeef -- solution.py",
    ) is not None


def test_search_space_opens_for_parallel_search(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".gp")
    frozen = runtime.freeze_spec(
        spec_with_host(project, "codex", strategy_name="random", max_candidates=2),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)

    opened = runtime.open_search_space(
        run_id,
        mode="enforce",
        reviewer_model="gpt-5.6-sol",
        reviewer_reasoning_effort="medium",
        reviewer_timeout_seconds=30,
    )

    assert opened["mode"] == "enforce"
    assert opened["schema_consolidation_interval"] == 20
    assert (space_dir(runtime, run_id) / "state.json").is_file()
    initial = space_dir(runtime, run_id) / "schemas" / "schema-000001.json"
    assert initial.is_file()
    assert initial.stat().st_mode & 0o222 == 0


def test_parallel_equivalent_plans_create_exactly_one_reservation(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidates = setup_parallel_runtime(tmp_path)
    reviewer = ConcurrentCollisionReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(runtime, run_id, "enforce")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                runtime.propose_search_space_plan,
                session_id,
                InterventionPlanProposal(
                    intervention="software-pipeline the inner loop at initiation interval 2",
                    scope="the same hot loop and public workload",
                    expected_new_information="whether II=2 reduces schedule cycles",
                ),
            )
            for _candidate_id, session_id, _workspace in candidates
        ]
        results = [future.result(timeout=10) for future in futures]

    assert sorted(result["decision"] for result in results) == ["accept", "reject"]
    [rejected] = [result for result in results if result["decision"] == "reject"]
    assert rejected["duplicate_of"] == [rejected["duplicate_plans"][0]["plan_id"]]
    assert rejected["duplicate_plans"][0]["coverage_status"] == "active_reservation"
    status = runtime.search_space_status(run_id)
    assert len(status["active_reservations"]) == 1
    assert status["active_collision_reviews"] == 1
    assert reviewer.calls == 3


def test_observe_mode_records_duplicate_without_blocking(tmp_path: Path) -> None:
    runtime, run_id, candidate_id, session_id, workspace = setup_runtime(tmp_path)
    runtime.search_space.reviewer = SamePointReviewer()  # type: ignore[assignment]
    open_search_space(runtime, run_id, "observe")

    first = runtime.propose_search_space_plan(session_id, proposal("covered"))
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        intervention_plan_id=first["plan_id"],
    )
    duplicate = runtime.propose_search_space_plan(session_id, proposal("duplicate"))

    assert duplicate == {"plan_id": "ip-0002", "decision": "accept"}
    status = runtime.search_space_status(run_id)
    assert status["semantic_duplicate_reviews"] == 1
    assert status["semantic_duplicate_probability"] == 0.5
    assert status["enforced_rejections"] == 0


def test_verifier_appends_immutable_evidence_and_next_review_sees_tail(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidate_id, session_id, workspace = setup_runtime(tmp_path)
    reviewer = ConsolidatingReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(runtime, run_id, "enforce")

    first = runtime.propose_search_space_plan(session_id, proposal("value-one"))
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        intervention_plan_id=first["plan_id"],
    )
    first_event_path = space_dir(runtime, run_id) / "events" / "se-000001.json"
    first_event_bytes = first_event_path.read_bytes()

    second = runtime.propose_search_space_plan(session_id, proposal("value-two"))
    search_state = reviewer.search_states[-1]
    assert search_state["schema_snapshot_version"] == 1
    assert search_state["coverage"] == []
    assert [event["event_id"] for event in search_state["tail_events"]] == [
        "se-000001"
    ]
    assert search_state["tail_events"][0]["realized_evidence"]["outcome"] == "neutral"
    prompt_evidence = search_state["tail_events"][0]["realized_evidence"]
    assert "diff_patch" not in prompt_evidence
    assert "+VALUE = 1" in prompt_evidence["diff_excerpt"]
    assert search_state["active_reservations"] == []

    (workspace / "initial_program.py").write_text("VALUE = 2\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        intervention_plan_id=second["plan_id"],
    )

    event_dir = space_dir(runtime, run_id) / "events"
    first_event = json.loads(first_event_path.read_text(encoding="utf-8"))
    second_event = json.loads(
        (event_dir / "se-000002.json").read_text(encoding="utf-8")
    )
    assert first_event_path.read_bytes() == first_event_bytes
    assert first_event_path.stat().st_mode & 0o222 == 0
    assert first_event["content_sha256"] == content_sha256(first_event)
    assert "+VALUE = 1" in first_event["realized_evidence"]["diff_patch"]
    assert second_event["content_sha256"] == content_sha256(second_event)
    assert second_event["previous_event_id"] == first_event["event_id"]
    assert second_event["previous_event_sha256"] == first_event["content_sha256"]

    first_plan = persisted_space_plan(runtime, run_id, first["plan_id"])
    assert first_plan["search_event_id"] == "se-000001"
    assert "realized_projection" not in first_plan
    assert "workspace_disposition" not in first_plan["realized_evidence"]


def test_spaceagent_receives_bounded_event_view_not_full_audit_diff(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    reviewer = ConsolidatingReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(runtime, run_id, "enforce")
    admission = runtime.propose_search_space_plan(session_id, proposal("large-delta"))
    runtime.search_space.begin_verifier(
        run_id=run_id,
        candidate_id=candidate_id,
        agent_session_id=session_id,
        plan_id=admission["plan_id"],
    )
    long_patch = "-VALUE = 0\n" + ("+changed = value\n" * 800)
    long_paths = [f"generated/{index:02d}-{'x' * 180}.py" for index in range(30)]
    long_symbols = [f"symbol_{index:02d}_{'y' * 180}" for index in range(30)]
    runtime.search_space.complete_verifier(
        run_id=run_id,
        plan_id=admission["plan_id"],
        iteration=1,
        score=0.0,
        process_passed=True,
        git_head="head-1",
        artifact_hash="artifact-1",
        realized_evidence=SpaceRealizedEvidence(
            artifact_delta_sha256="delta-1",
            delta_files=long_paths,
            changed_symbols=long_symbols,
            diff_stat="s" * 2_000,
            diff_patch=long_patch,
            diff_truncated=True,
            metric_name="combined_score",
            metric_direction="maximize",
            score_before=0.0,
            score_after=0.0,
            score_delta=0.0,
            outcome="neutral",
            validity_passed=True,
            process_passed=True,
            completed_at="2026-07-20T00:00:01Z",
        ),
    )

    runtime.propose_search_space_plan(session_id, proposal("inspect-view"))

    [event_view] = reviewer.search_states[-1]["tail_events"]
    realized_view = event_view["realized_evidence"]
    assert "diff_patch" not in realized_view
    assert len(realized_view["diff_excerpt"]) == 1_000
    assert len(realized_view["diff_stat"]) == 400
    assert len(realized_view["delta_files"]) <= 12
    assert sum(map(len, realized_view["delta_files"])) <= 1_200
    assert realized_view["delta_file_count"] == 30
    assert len(realized_view["changed_symbols"]) <= 12
    assert sum(map(len, realized_view["changed_symbols"])) <= 1_200
    assert realized_view["changed_symbol_count"] == 30

    persisted_event = json.loads(
        (
            space_dir(runtime, run_id) / "events" / "se-000001.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted_event["realized_evidence"]["diff_patch"] == long_patch


def test_ordinary_admission_uses_representative_schema_refs() -> None:
    entry = SpaceCoverageEntry(
        coverage_id="shared-cell",
        description="same semantic point",
        context="same workload",
        evidence_event_ids=[f"se-{index:06d}" for index in range(1, 11)],
        evidence_plan_ids=[f"ip-{index:04d}" for index in range(1, 11)],
        outcomes=["neutral"],
    )

    ordinary = FileSearchSpaceRuntime._review_coverage_view(
        entry,
        include_all_refs=False,
    )
    checkpoint = FileSearchSpaceRuntime._review_coverage_view(
        entry,
        include_all_refs=True,
    )

    assert ordinary["evidence_event_ids"] == [
        "se-000001",
        "se-000002",
        "se-000009",
        "se-000010",
    ]
    assert ordinary["evidence_event_count"] == 10
    assert ordinary["refs_truncated"] is True
    assert checkpoint["evidence_event_ids"] == entry.evidence_event_ids
    assert "refs_truncated" not in checkpoint


def test_periodic_schema_snapshots_append_and_retain_coverage(tmp_path: Path) -> None:
    runtime, run_id, candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    reviewer = ConsolidatingReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(
        runtime,
        run_id,
        "enforce",
        schema_consolidation_interval=2,
    )
    schemas_dir = space_dir(runtime, run_id) / "schemas"
    initial_path = schemas_dir / "schema-000001.json"
    initial_bytes = initial_path.read_bytes()

    for iteration in range(1, 5):
        admission = runtime.propose_search_space_plan(
            session_id,
            proposal(f"point-{iteration}"),
        )
        complete_space_plan(
            runtime,
            run_id=run_id,
            candidate_id=candidate_id,
            session_id=session_id,
            plan_id=admission["plan_id"],
            iteration=iteration,
        )

    schema_two_path = schemas_dir / "schema-000002.json"
    schema_two_bytes = schema_two_path.read_bytes()
    runtime.propose_search_space_plan(session_id, proposal("second-checkpoint"))

    snapshots = sorted(schemas_dir.glob("schema-*.json"))
    assert [path.name for path in snapshots] == [
        "schema-000001.json",
        "schema-000002.json",
        "schema-000003.json",
    ]
    assert initial_path.read_bytes() == initial_bytes
    assert schema_two_path.read_bytes() == schema_two_bytes
    assert all(path.stat().st_mode & 0o222 == 0 for path in snapshots)

    schema_two = json.loads(schema_two_bytes)
    schema_three = json.loads(snapshots[-1].read_text(encoding="utf-8"))
    assert schema_three["parent_snapshot_version"] == 2
    assert schema_three["parent_snapshot_sha256"] == schema_two["content_sha256"]
    assert schema_three["built_through_event_id"] == "se-000004"
    covered_events = {
        event_id
        for entry in schema_three["coverage"]
        for event_id in entry["evidence_event_ids"]
    }
    assert covered_events == {f"se-{index:06d}" for index in range(1, 5)}
    compacted_calls = [
        covered_ids
        for state, covered_ids in zip(
            reviewer.search_states,
            reviewer.covered_plan_ids,
            strict=True,
        )
        if state["schema_snapshot_version"] == 2
    ]
    assert compacted_calls
    assert all("ip-0001" not in ids and "ip-0002" not in ids for ids in compacted_calls)
    status = runtime.search_space_status(run_id)
    assert status["schema_snapshot_version"] == 3
    assert status["schema_tail_event_count"] == 0


def test_schema_checkpoint_repairs_omitted_evidence_references(tmp_path: Path) -> None:
    runtime, run_id, candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    reviewer = OmittingConsolidatingReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(
        runtime,
        run_id,
        "enforce",
        schema_consolidation_interval=2,
    )

    for iteration in (1, 2):
        admission = runtime.propose_search_space_plan(
            session_id,
            proposal(f"omitted-ref-{iteration}"),
        )
        complete_space_plan(
            runtime,
            run_id=run_id,
            candidate_id=candidate_id,
            session_id=session_id,
            plan_id=admission["plan_id"],
            iteration=iteration,
        )

    status = runtime.search_space_status(run_id)
    covered_events = {
        event_id
        for entry in status["schema_coverage"]
        for event_id in entry["evidence_event_ids"]
    }
    assert covered_events == {"se-000001", "se-000002"}
    assert status["schema_built_through_event_id"] == "se-000002"
    assert status["schema_consolidation_attempts"] == 1
    assert status["schema_consolidation_successes"] == 1
    assert status["schema_consolidation_failures"] == 0
    assert status["reviewer_fail_open"] == 0
    schema_views = status["schema_coverage"]
    assert schema_views
    snapshot = json.loads(
        (
            space_dir(runtime, run_id)
            / "schemas"
            / "schema-000002.json"
        ).read_text(encoding="utf-8")
    )
    assert set(snapshot["space_schema"]["views"]) == set(SPACE_VIEW_NAMES)
    persisted = persisted_space_plan(runtime, run_id, "ip-0002")
    assert "schema_update" not in persisted["review"]


def test_schema_failure_keeps_tail_without_failing_admission(tmp_path: Path) -> None:
    runtime, run_id, candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    reviewer = BrokenConsolidatingReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(
        runtime,
        run_id,
        "enforce",
        schema_consolidation_interval=2,
    )

    for iteration in (1, 2):
        admission = runtime.propose_search_space_plan(
            session_id,
            proposal(f"schema-failure-{iteration}"),
        )
        complete_space_plan(
            runtime,
            run_id=run_id,
            candidate_id=candidate_id,
            session_id=session_id,
            plan_id=admission["plan_id"],
            iteration=iteration,
        )

    status = runtime.search_space_status(run_id)
    assert status["schema_snapshot_version"] == 1
    assert status["schema_tail_event_count"] == 2
    assert status["schema_consolidation_attempts"] == 1
    assert status["schema_consolidation_failures"] == 1
    assert "synthetic schema reviewer failure" in status[
        "last_schema_consolidation_error"
    ]
    assert status["reviewer_fail_open"] == 0

    next_admission = runtime.propose_search_space_plan(
        session_id,
        proposal("admission-after-schema-failure"),
    )
    assert next_admission["decision"] == "accept"
    persisted = persisted_space_plan(runtime, run_id, next_admission["plan_id"])
    assert persisted["admission_source"] == "reviewer"
    assert persisted["reviewer_error"] is None


def test_concurrent_checkpoint_commits_frozen_watermark_and_keeps_new_tail(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidates = setup_parallel_runtime(tmp_path)
    reviewer = BlockingConsolidatingReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(
        runtime,
        run_id,
        "enforce",
        schema_consolidation_interval=2,
    )
    admissions = [
        runtime.propose_search_space_plan(session_id, proposal(f"seed-{index}"))
        for index, (_candidate_id, session_id, _workspace) in enumerate(
            candidates,
            start=1,
        )
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                complete_space_plan,
                runtime,
                run_id=run_id,
                candidate_id=candidate_id,
                session_id=session_id,
                plan_id=admission["plan_id"],
                iteration=index,
            )
            for index, ((candidate_id, session_id, _workspace), admission) in enumerate(
                zip(candidates, admissions, strict=True),
                start=1,
            )
        ]
        assert reviewer.started.wait(timeout=5)
        candidate_id, session_id, _workspace = candidates[0]
        tail_admission = runtime.propose_search_space_plan(
            session_id,
            proposal("arrived-during-schema-review"),
        )
        complete_space_plan(
            runtime,
            run_id=run_id,
            candidate_id=candidate_id,
            session_id=session_id,
            plan_id=tail_admission["plan_id"],
            iteration=3,
        )
        reviewer.release.set()
        for future in futures:
            future.result(timeout=5)

    status = runtime.search_space_status(run_id)
    assert status["evidence_event_count"] == 3
    assert status["schema_built_through_event_id"] == "se-000002"
    assert status["schema_tail_event_count"] == 1
    assert status["schema_consolidation_attempts"] == 1
    assert status["schema_consolidation_successes"] == 1
    assert status["schema_consolidation_failures"] == 0
    assert reviewer.checkpoint_calls == 1


def test_reviewer_can_reject_plan_referenced_only_by_compacted_schema(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    reviewer = SchemaDuplicateReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(
        runtime,
        run_id,
        "enforce",
        schema_consolidation_interval=2,
    )
    for iteration in (1, 2):
        admission = runtime.propose_search_space_plan(
            session_id,
            proposal(f"covered-{iteration}"),
        )
        complete_space_plan(
            runtime,
            run_id=run_id,
            candidate_id=candidate_id,
            session_id=session_id,
            plan_id=admission["plan_id"],
            iteration=iteration,
        )
    duplicate = runtime.propose_search_space_plan(session_id, proposal("duplicate"))

    assert duplicate["decision"] == "reject"
    assert duplicate["duplicate_of"] == ["ip-0001"]
    assert duplicate["duplicate_plans"][0]["coverage_status"] == (
        "completed_coverage"
    )
    assert reviewer.covered_plan_ids[-1] == []


def test_invalid_and_infrastructure_events_are_audited_not_coverage(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    reviewer = ConsolidatingReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(
        runtime,
        run_id,
        "enforce",
        schema_consolidation_interval=2,
    )

    outcomes = [
        "invalid",
        "infrastructure_failure",
        "invalid",
        "infrastructure_failure",
    ]
    for iteration, outcome in enumerate(outcomes, start=1):
        admission = runtime.propose_search_space_plan(
            session_id,
            proposal(f"ineligible-{iteration}"),
        )
        complete_space_plan(
            runtime,
            run_id=run_id,
            candidate_id=candidate_id,
            session_id=session_id,
            plan_id=admission["plan_id"],
            iteration=iteration,
            outcome=outcome,
        )

    runtime.propose_search_space_plan(session_id, proposal("checkpoint"))

    status = runtime.search_space_status(run_id)
    assert status["evidence_event_count"] == 4
    assert status["completed_coverage"] == []
    assert status["schema_coverage"] == []
    assert status["schema_built_through_event_id"] == "se-000004"
    assert status["schema_tail_event_count"] == 0
    event_payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((space_dir(runtime, run_id) / "events").glob("*.json"))
    ]
    assert [event["coverage_eligible"] for event in event_payloads] == [
        False,
        False,
        False,
        False,
    ]


def test_schema_checkpoint_uses_one_global_claim_before_parallel_admission(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidates = setup_parallel_runtime(tmp_path)
    reviewer = ConsolidatingReviewer()
    runtime.search_space.reviewer = reviewer  # type: ignore[assignment]
    open_search_space(
        runtime,
        run_id,
        "enforce",
        schema_consolidation_interval=2,
    )

    for iteration, (candidate_id, session_id, _workspace) in enumerate(
        candidates,
        start=1,
    ):
        admission = runtime.propose_search_space_plan(
            session_id,
            proposal(f"seed-{iteration}"),
        )
        complete_space_plan(
            runtime,
            run_id=run_id,
            candidate_id=candidate_id,
            session_id=session_id,
            plan_id=admission["plan_id"],
            iteration=iteration,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                runtime.propose_search_space_plan,
                session_id,
                proposal(f"parallel-{index}"),
            )
            for index, (_candidate_id, session_id, _workspace) in enumerate(
                candidates,
                start=1,
            )
        ]
        admissions = [future.result(timeout=10) for future in futures]

    assert [admission["decision"] for admission in admissions] == ["accept", "accept"]
    snapshots = sorted((space_dir(runtime, run_id) / "schemas").glob("schema-*.json"))
    assert [path.name for path in snapshots] == [
        "schema-000001.json",
        "schema-000002.json",
    ]
    status = runtime.search_space_status(run_id)
    assert status["schema_snapshot_version"] == 2
    assert len(status["active_reservations"]) == 2
    assert reviewer.checkpoint_calls == 1
    assert reviewer.calls >= 4


def test_repeated_semantic_rejections_flag_candidate_spinning(tmp_path: Path) -> None:
    runtime, run_id, candidate_id, session_id, workspace = setup_runtime(tmp_path)
    runtime.search_space.reviewer = SamePointReviewer()  # type: ignore[assignment]
    open_search_space(runtime, run_id, "enforce")

    first = runtime.propose_search_space_plan(session_id, proposal("covered"))
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        intervention_plan_id=first["plan_id"],
    )
    for index in range(3):
        result = runtime.propose_search_space_plan(
            session_id,
            proposal(f"paraphrase-{index}"),
        )
        assert result["decision"] == "reject"

    status = runtime.search_space_status(run_id)
    [signal] = status["candidate_loop_signals"]
    assert signal == {
        "candidate_id": candidate_id,
        "consecutive_duplicate_reviews": 3,
        "possible_spinning": True,
        "repeated_conflict_refs": ["ip-0001"],
        "repeated_point_keys": ["same-point"],
        "repeated_region_keys": ["same-region"],
    }


def test_candidate_response_never_exposes_spaceagent_reasoning(tmp_path: Path) -> None:
    runtime, run_id, candidate_id, session_id, workspace = setup_runtime(tmp_path)
    runtime.search_space.reviewer = SamePointReviewer()  # type: ignore[assignment]
    open_search_space(runtime, run_id, "enforce")
    first = runtime.propose_search_space_plan(session_id, proposal("covered"))
    (workspace / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        intervention_plan_id=first["plan_id"],
    )

    response = runtime.propose_search_space_plan(session_id, proposal("duplicate"))

    assert set(response) == {
        "plan_id",
        "decision",
        "duplicate_of",
        "duplicate_plans",
    }
    assert response["duplicate_of"] == ["ip-0001"]
    assert "rationale" not in response
    assert "overlap" not in response
    persisted = persisted_space_plan(runtime, run_id, response["plan_id"])
    assert persisted["review"]["rationale"] == "audit-only duplicate rationale"


def test_invalid_execution_appends_evidence_without_exhausting_point(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidate_id, session_id, workspace = setup_runtime(tmp_path)
    runtime.search_space.reviewer = ConsolidatingReviewer()  # type: ignore[assignment]
    open_search_space(runtime, run_id, "enforce")
    admission = runtime.propose_search_space_plan(session_id, proposal("invalid"))
    (workspace / "config.yaml").write_text("name: forbidden-change\n", encoding="utf-8")

    report = runtime.run_verifier(
        run_id,
        candidate_id,
        agent_session_id=session_id,
        intervention_plan_id=admission["plan_id"],
    )

    assert report.process_passed is False
    plan = persisted_space_plan(runtime, run_id, admission["plan_id"])
    assert plan["coverage_eligible"] is False
    assert plan["search_event_id"] == "se-000001"
    assert plan["realized_evidence"]["outcome"] == "invalid"
    status = runtime.search_space_status(run_id)
    assert status["completed_coverage"] == []
    assert status["evidence_event_count"] == 1
    assert status["realized_outcomes"] == {"invalid": 1}


def test_state_load_repairs_crash_between_plan_and_state_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run_id, candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    open_experiment(runtime, run_id, "b1")
    admission = runtime.propose_search_space_plan(session_id, proposal("crash"))
    runtime.search_space.begin_verifier(
        run_id=run_id,
        candidate_id=candidate_id,
        agent_session_id=session_id,
        plan_id=admission["plan_id"],
    )
    original_write_state = runtime.search_space._write_state

    def fail_state_write(*_args, **_kwargs):
        raise OSError("synthetic crash before state commit")

    monkeypatch.setattr(runtime.search_space, "_write_state", fail_state_write)
    with pytest.raises(OSError, match="synthetic crash"):
        runtime.search_space.complete_verifier(
            run_id=run_id,
            plan_id=admission["plan_id"],
            iteration=1,
            score=0.0,
            process_passed=True,
            git_head="head-1",
            artifact_hash="artifact-1",
            realized_evidence=SpaceRealizedEvidence(
                metric_name="combined_score",
                metric_direction="maximize",
                outcome="neutral",
                validity_passed=True,
                process_passed=True,
                completed_at="2026-07-20T00:00:01Z",
            ),
        )
    monkeypatch.setattr(runtime.search_space, "_write_state", original_write_state)

    status = runtime.search_space_status(run_id)
    assert status["active_reservations"] == []
    assert status["completed_coverage"] == [admission["plan_id"]]
    assert status["evidence_revision"] == 1
    assert status["evidence_event_head"] == "se-000001"


def test_state_load_repairs_crash_after_evidence_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run_id, candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    open_experiment(runtime, run_id, "b1")
    admission = runtime.propose_search_space_plan(session_id, proposal("event-crash"))
    runtime.search_space.begin_verifier(
        run_id=run_id,
        candidate_id=candidate_id,
        agent_session_id=session_id,
        plan_id=admission["plan_id"],
    )
    original_write_plan = runtime.search_space._write_plan

    def fail_completed_plan_write(plan):
        if plan.status == "completed":
            raise OSError("synthetic crash after evidence publish")
        original_write_plan(plan)

    monkeypatch.setattr(runtime.search_space, "_write_plan", fail_completed_plan_write)
    with pytest.raises(OSError, match="after evidence publish"):
        runtime.search_space.complete_verifier(
            run_id=run_id,
            plan_id=admission["plan_id"],
            iteration=1,
            score=0.0,
            process_passed=True,
            git_head="head-1",
            artifact_hash="artifact-1",
            realized_evidence=SpaceRealizedEvidence(
                metric_name="combined_score",
                metric_direction="maximize",
                outcome="neutral",
                validity_passed=True,
                process_passed=True,
                completed_at="2026-07-20T00:00:01Z",
            ),
        )
    monkeypatch.setattr(runtime.search_space, "_write_plan", original_write_plan)

    status = runtime.search_space_status(run_id)

    assert status["active_reservations"] == []
    assert status["completed_coverage"] == [admission["plan_id"]]
    recovered = json.loads(
        (
            runtime.root_dir
            / "runs"
            / run_id
            / "space-experiment"
            / "plans"
            / f"{admission['plan_id']}.json"
        ).read_text(encoding="utf-8")
    )
    assert recovered["status"] == "completed"
    assert recovered["search_event_id"] == "se-000001"
    assert recovered["verifier"]["recovered_from_search_event"] == "se-000001"


def test_state_load_advances_published_schema_head_without_overwrite(
    tmp_path: Path,
) -> None:
    runtime, run_id, _candidate_id, _session_id, _reviewer, snapshot_path = (
        build_second_schema_snapshot(tmp_path)
    )
    snapshot_bytes = snapshot_path.read_bytes()
    state_path = space_dir(runtime, run_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["schema_revision"] = 1
    state_path.write_text(json.dumps(state), encoding="utf-8")

    status = runtime.search_space_status(run_id)

    assert status["schema_revision"] == 2
    assert status["schema_snapshot_version"] == 2
    assert snapshot_path.read_bytes() == snapshot_bytes
    repaired = json.loads(state_path.read_text(encoding="utf-8"))
    assert repaired["schema_revision"] == 2


def test_same_plan_retry_recovers_reviewing_and_accepted_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run_id, _candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    open_experiment(runtime, run_id, "b1")
    original_accept = runtime.search_space._accept_without_review

    def interrupt_before_review(*_args, **_kwargs):
        raise OSError("synthetic response-path interruption")

    monkeypatch.setattr(
        runtime.search_space,
        "_accept_without_review",
        interrupt_before_review,
    )
    with pytest.raises(OSError, match="response-path interruption"):
        runtime.propose_search_space_plan(session_id, proposal("retry"))
    context = runtime.get_agent_context(session_id)["search_space"]
    assert context["outstanding_plan_status"] == "reviewing"

    monkeypatch.setattr(runtime.search_space, "_accept_without_review", original_accept)
    recovered = runtime.propose_search_space_plan(session_id, proposal("retry"))
    repeated = runtime.propose_search_space_plan(session_id, proposal("retry"))

    assert recovered == {"plan_id": "ip-0001", "decision": "accept"}
    assert repeated == recovered
    assert runtime.search_space_status(run_id)["plans_total"] == 1


def test_evidence_content_hash_tampering_is_rejected(tmp_path: Path) -> None:
    runtime, run_id, candidate_id, session_id, _workspace = setup_runtime(tmp_path)
    runtime.search_space.reviewer = ConsolidatingReviewer()  # type: ignore[assignment]
    open_search_space(runtime, run_id, "enforce")
    admission = runtime.propose_search_space_plan(session_id, proposal("tamper"))
    complete_space_plan(
        runtime,
        run_id=run_id,
        candidate_id=candidate_id,
        session_id=session_id,
        plan_id=admission["plan_id"],
        iteration=1,
    )
    event_path = space_dir(runtime, run_id) / "events" / "se-000001.json"
    event_path.chmod(0o644)
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["proposal"]["intervention"] = "rewritten fact"
    event_path.write_text(json.dumps(event), encoding="utf-8")

    with pytest.raises(RuntimeError, match="evidence event content hash mismatch"):
        runtime.search_space_status(run_id)


def test_schema_parent_chain_tampering_is_rejected(tmp_path: Path) -> None:
    runtime, run_id, _candidate_id, _session_id, _reviewer, snapshot_path = (
        build_second_schema_snapshot(tmp_path)
    )
    snapshot_path.chmod(0o644)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["parent_snapshot_sha256"] = "0" * 64
    snapshot["content_sha256"] = content_sha256(snapshot)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(RuntimeError, match="broken schema snapshot parent chain"):
        runtime.search_space_status(run_id)
