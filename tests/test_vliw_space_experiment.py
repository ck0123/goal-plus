from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

from goal_plus.space_agent import (
    ReviewerExecution,
    SearchSpaceConfig,
    SchemaReviewerExecution,
    SocketSpaceReviewer,
    SpaceCoverageEntry,
    SpaceOverlap,
    SpaceReviewDecision,
    SpaceSchemaUpdate,
)


ROOT = Path(__file__).resolve().parents[1]
WORKER = (
    ROOT
    / "examples-hide"
    / "vliw_kernel_optimization"
    / "worker-codex-gp"
)


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load_module("vliw_space_run_experiment", WORKER / "run_experiment.py")


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class PreflightReviewer:
    def __init__(self) -> None:
        self.review_calls = 0
        self.consolidation_calls = 0

    def review(self, _config, _proposal, _covered) -> ReviewerExecution:
        self.review_calls += 1
        return ReviewerExecution(
            result=SpaceReviewDecision(
                decision="accept",
                duplicate_of=[],
                reason_code="novel",
                overlap=SpaceOverlap(
                    artifact="none",
                    configuration="none",
                    mechanism="none",
                    context="none",
                    epistemic="none",
                    behavior="none",
                ),
                confidence=1.0,
                rationale="transport preflight",
            ),
            latency_ms=2,
            usage={"input_tokens": 10},
        )

    def consolidate(self, config) -> SchemaReviewerExecution:
        self.consolidation_calls += 1
        schema = json.loads(json.dumps(config.space_schema))
        runtime_state = schema.pop("_runtime_search_state")
        event = runtime_state["tail_events"][0]
        return SchemaReviewerExecution(
            result=SpaceSchemaUpdate(
                space_schema=schema,
                coverage=[
                    SpaceCoverageEntry(
                        coverage_id="preflight",
                        description=event["proposal"]["intervention"],
                        context=event["proposal"]["scope"],
                        evidence_event_ids=[event["event_id"]],
                        evidence_plan_ids=[event["plan_id"]],
                        outcomes=[event["realized_evidence"]["outcome"]],
                    )
                ],
                revision_summary="Consolidated synthetic preflight Evidence.",
                revision_evidence_event_ids=[event["event_id"]],
            ),
            latency_ms=3,
            usage={"input_tokens": 20},
        )


def test_current_config_is_three_lane_enforce() -> None:
    config = RUNNER.load_config(WORKER / "experiment.json")

    assert config["mode"] == "enforce"
    assert config["candidate_count"] == 3
    assert config["max_parallel"] == 3
    assert config["min_runtime_seconds"] == 3600
    assert config["reviewer_profile"] == "inherited_codex_home"
    assert config["reviewer_transport"] == "plain_json"
    assert config["schema_consolidation_interval"] == 20

    schema = json.loads((WORKER / "space-schema.json").read_text(encoding="utf-8"))
    duplicate_policy = schema["duplicate_policy"]
    assert "RelevantBaseProjection" in duplicate_policy["experiment_identity"]
    assert "hashes establish exact identity and provenance only" in (
        duplicate_policy["base_projection_policy"]
    )


def test_rendered_prompt_has_no_markers_and_uses_worker_source() -> None:
    config = RUNNER.load_config(WORKER / "experiment.json")
    prompt = RUNNER.render_prompt(config, "test-current-space")

    assert prompt.startswith("/goal-plus mode=autonomous")
    assert "__" not in prompt
    assert f"source_path is exactly `{WORKER}`" in prompt
    assert "budget.max_candidates=3" in prompt
    assert 'mode="enforce"' in prompt
    assert "schema_consolidation_interval=20" in prompt
    assert "search_start_agent_session exactly once for each candidate" in prompt
    assert "lifecycle operations, not new experiments" in prompt


def test_reviewer_preflight_checks_admission_and_schema_consolidation() -> None:
    config = RUNNER.load_config(WORKER / "experiment.json")
    reviewer = PreflightReviewer()

    result = RUNNER.reviewer_preflight(reviewer, config, "preflight-test")

    assert result["decision"] == "accept"
    assert result["schema_consolidation"]["target_event_id"] == "se-000001"
    assert reviewer.review_calls == 1
    assert reviewer.consolidation_calls == 1


def test_reviewer_socket_routes_schema_consolidation(tmp_path: Path) -> None:
    schema = json.loads((WORKER / "space-schema.json").read_text(encoding="utf-8"))
    schema["_runtime_search_state"] = {
        "tail_events": [
            {
                "event_id": "se-000001",
                "plan_id": "ip-0001",
                "proposal": {
                    "intervention": "synthetic change",
                    "scope": "synthetic scope",
                },
                "realized_evidence": {"outcome": "neutral"},
            }
        ]
    }
    config = SearchSpaceConfig(
        experiment_id="socket-preflight",
        run_id="run_socket_preflight",
        mode="enforce",
        schema_path="space-schema.json",
        schema_sha256="synthetic",
        space_schema=schema,
        reviewer_model="test-model",
        reviewer_reasoning_effort="medium",
        reviewer_timeout_seconds=30,
        schema_consolidation_interval=20,
        created_at="2026-07-20T00:00:00Z",
    )
    address = str(tmp_path / "space-review.sock")
    reviewer = PreflightReviewer()
    server = RUNNER.ConcurrentSpaceReviewerServer(
        address,
        reviewer,
        max_concurrent_reviews=1,
    )
    server.start()
    try:
        execution = SocketSpaceReviewer(address).consolidate(config)
    finally:
        server.stop()

    assert execution.result.revision_evidence_event_ids == ["se-000001"]
    assert reviewer.consolidation_calls == 1


def test_parse_evaluation_requires_correct_sections() -> None:
    text = """
===== public / local =====
all_correct=True
score_cycles=4550
Score: 4550.00

===== hidden / local =====
all_correct=False
score_cycles=None
Score: 0.00
"""

    assert RUNNER.parse_evaluation(text) == {
        "public_cycles": 4550.0,
        "hidden_cycles": None,
    }


def test_space_summary_reports_same_and_cross_lane_duplicates(tmp_path: Path) -> None:
    space = tmp_path / "runs" / "run_1" / "search-space"
    write_json(space / "config.json", {"mode": "enforce"})
    write_json(
        space / "state.json",
        {
            "state_version": 8,
            "admission_revision": 5,
            "evidence_revision": 1,
            "schema_revision": 2,
            "schema_consolidation_attempts": 1,
            "schema_consolidation_successes": 1,
            "schema_consolidation_failures": 0,
            "schema_reviewer_latency_ms_total": 3000,
            "schema_reviewer_usage": {"input_tokens": 20},
            "last_schema_consolidation_error": None,
        },
    )
    common_proposal = {
        "intervention": "change schedule",
        "scope": "Kernel.build",
        "expected_new_information": "whether cycles fall",
    }
    write_json(
        space / "plans" / "ip-0001.json",
        {
            "plan_id": "ip-0001",
            "candidate_id": "c001",
            "status": "completed",
            "created_at": "2026-07-20T00:00:00Z",
            "admission_source": "reviewer",
            "review_attempts": 1,
            "proposal": common_proposal,
            "review": {"decision": "accept", "reason_code": "novel"},
            "realized_evidence": {"outcome": "improved"},
        },
    )
    write_json(
        space / "plans" / "ip-0002.json",
        {
            "plan_id": "ip-0002",
            "candidate_id": "c002",
            "status": "rejected",
            "created_at": "2026-07-20T00:21:00Z",
            "admission_source": "reviewer",
            "review_attempts": 2,
            "proposal": common_proposal,
            "review": {
                "decision": "reject",
                "reason_code": "duplicate_prior_intervention",
                "duplicate_of": ["ip-0001"],
                "confidence": 0.9,
            },
        },
    )
    write_json(
        space / "plans" / "ip-0003.json",
        {
            "plan_id": "ip-0003",
            "candidate_id": "c001",
            "status": "rejected",
            "created_at": "2026-07-20T00:41:00Z",
            "admission_source": "reviewer",
            "review_attempts": 1,
            "proposal": common_proposal,
            "review": {
                "decision": "reject",
                "reason_code": "active_plan_collision",
                "duplicate_of": ["ip-0001"],
                "confidence": 0.8,
            },
        },
    )
    write_json(space / "events" / "se-000001.json", {"event_id": "se-000001"})
    write_json(space / "schemas" / "schema-000001.json", {"snapshot_version": 1})
    write_json(space / "schemas" / "schema-000002.json", {"snapshot_version": 2})

    summary = RUNNER.space_summary(tmp_path, "run_1")

    assert summary["plan_count"] == 3
    assert summary["duplicate_rejection_count"] == 2
    assert summary["duplicate_rejection_rate"] == 2 / 3
    assert summary["same_lane_rejection_count"] == 1
    assert summary["cross_lane_rejection_count"] == 1
    assert summary["review_retry_count"] == 1
    assert summary["by_elapsed_minutes"]["20-40"]["rejection_rate"] == 1.0
    assert summary["evidence_event_count"] == 1
    assert summary["schema_snapshot_count"] == 2
    assert summary["schema_consolidation"] == {
        "schema_consolidation_attempts": 1,
        "schema_consolidation_successes": 1,
        "schema_consolidation_failures": 0,
        "schema_reviewer_latency_ms_total": 3000,
        "schema_reviewer_usage": {"input_tokens": 20},
        "last_schema_consolidation_error": None,
    }


def test_post_search_evaluates_selected_commit_not_ledger_head(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / ".gp"
    run_dir = runtime_root / "runs" / "run_1"
    workspace = run_dir / "workspace" / "c003"
    workspace.mkdir(parents=True)
    (workspace / "solution.py").write_text("CURRENT = 1\n", encoding="utf-8")
    write_json(
        run_dir / "run.json",
        {
            "run_id": "run_1",
            "state": "promoted",
            "selected_candidate_id": "c003",
            "selected_iteration": 21,
            "selected_git_head": "selected-head",
            "best_score": 1374.0,
        },
    )
    write_json(run_dir / "search-space" / "config.json", {"mode": "enforce"})
    write_json(run_dir / "search-space" / "state.json", {})
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    def fake_checked(command, *, cwd, capture=False):
        assert cwd == workspace
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(stdout="ledger-head\n")
        assert command == ["git", "show", "selected-head:solution.py"]
        return SimpleNamespace(stdout="SELECTED = 1\n")

    evaluation = """
===== public / local =====
all_correct=True
score_cycles=1374
===== hidden / local =====
all_correct=True
score_cycles=1400
"""
    monkeypatch.setattr(RUNNER, "checked", fake_checked)
    monkeypatch.setattr(
        RUNNER.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=evaluation,
            stderr="",
            returncode=0,
        ),
    )

    result = RUNNER.post_search_evaluate(runtime_root, output_dir)

    assert result["evaluation_passed"] is True
    assert result["workspace_git_head"] == "ledger-head"
    assert result["evaluated_git_head"] == "selected-head"
    assert result["workspace_matches_selected_solution"] is False
    selected = Path(result["candidate_solution"])
    assert selected.read_text(encoding="utf-8") == "SELECTED = 1\n"


def test_isolated_command_keeps_worker_writable_and_masks_hidden_inputs(
    tmp_path: Path,
) -> None:
    config = RUNNER.load_config(WORKER / "experiment.json")
    command = RUNNER.isolated_codex_command(
        codex="codex",
        run_dir=tmp_path,
        output_path=tmp_path / "final.txt",
        config=config,
    )

    worker_index = command.index(str(WORKER))
    assert command[worker_index - 1] == "--bind"
    for hidden in RUNNER.hidden_paths():
        index = command.index(str(hidden))
        assert command[index - 1] == "--tmpfs"
