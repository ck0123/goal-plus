from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from agentic_any_search_mcp.models import (
    ArtifactBundle,
    CandidateTask,
    FrozenSpec,
    RunState,
    RunSummary,
    ScoreReport,
    SearchSpec,
    VerifierResult,
)
from agentic_any_search_mcp.tools import SearchTools


def spec_dict() -> dict:
    return {
        "objective": "maximize toy score",
        "metric_name": "combined_score",
        "metric_direction": "maximize",
        "source_path": ".",
        "edit_surface": {
            "allow": ["initial_program.py"],
            "deny": ["evaluator.py"],
        },
        "budget": {
            "max_candidates": 4,
            "max_parallel": 2,
            "wall_clock_seconds": 300,
        },
        "process_verifiers": [
            {
                "name": "score",
                "role": "ranking_signal",
                "command": ["python", "evaluator.py"],
            }
        ],
    }


def frozen_spec() -> FrozenSpec:
    spec = SearchSpec.model_validate(spec_dict())
    return FrozenSpec(
        frozen_spec_id="spec_123",
        spec_hash="hash",
        spec=spec,
        verifier_hashes={"evaluator.py": "abc"},
        frozen_verifier_paths={"evaluator.py": "/tmp/evaluator.py"},
        created_at="2026-06-24T00:00:00Z",
    )


def test_search_freeze_spec_converts_input_and_serializes_output() -> None:
    runtime = Mock()
    runtime.freeze_spec.return_value = frozen_spec()
    tools = SearchTools(runtime)

    result = tools.search_freeze_spec(spec_dict(), ["evaluator.py"])

    spec_arg, paths_arg = runtime.freeze_spec.call_args.args
    assert isinstance(spec_arg, SearchSpec)
    assert paths_arg == [Path("evaluator.py")]
    assert result["frozen_spec_id"] == "spec_123"
    assert result["spec"]["process_verifiers"][0]["role"] == "ranking_signal"


def test_search_tools_delegate_runtime_calls_with_models() -> None:
    runtime = Mock()
    runtime.create_run.return_value = "run_1"
    runtime.status.return_value = RunSummary(
        run_id="run_1",
        state=RunState.RUNNING,
        frozen_spec_id="spec_123",
        candidates_total=0,
        candidates_running=0,
        candidates_evaluated=0,
    )
    runtime.list_history.return_value = {
        "run_id": "run_1",
        "candidates": [],
    }
    runtime.next_batch.return_value = [
        CandidateTask(
            run_id="run_1",
            candidate_id="c001",
            hypothesis="try",
            workspace=Path("/tmp/c001"),
            allowed_files=["initial_program.py"],
            denied_files=["evaluator.py"],
        )
    ]
    runtime.run_verifier.return_value = ScoreReport(
        run_id="run_1",
        candidate_id="c001",
        validity_passed=True,
        process_passed=True,
        aggregate_score=1.0,
        verifier_results=[
            VerifierResult(
                name="score",
                role="ranking_signal",
                passed=True,
                score=1.0,
            )
        ],
    )
    runtime.select.return_value = {"selected_candidate_id": "c001"}
    runtime.report.return_value = Path("/tmp/report.md")
    runtime.promote.return_value = Path("/tmp/c001.patch")
    tools = SearchTools(runtime)

    assert tools.search_create("spec_123") == {"run_id": "run_1"}
    assert tools.search_status("run_1")["state"] == "running"
    assert tools.search_list_history("run_1", top_n=3, sort_by="created") == {
        "run_id": "run_1",
        "candidates": [],
    }
    assert tools.search_next_batch("run_1", 1)[0]["candidate_id"] == "c001"
    assert tools.search_run_verifier("run_1", "c001")["aggregate_score"] == 1.0
    assert tools.search_select("run_1") == {"selected_candidate_id": "c001"}
    assert tools.search_report("run_1") == {"report_path": "/tmp/report.md"}
    assert tools.search_promote("run_1", "c001") == {"artifact_path": "/tmp/c001.patch"}
    assert tools.search_abort("run_1", "stop") == {"aborted": True}

    submit_result = tools.search_submit_candidate(
        "run_1",
        "c001",
        {"candidate_id": "c001", "status": "patch_ready"},
    )
    artifact = runtime.submit_candidate.call_args.kwargs["artifact"]
    assert submit_result == {"accepted": True}
    assert isinstance(artifact, ArtifactBundle)
    runtime.abort.assert_called_once_with("run_1", "stop")
    runtime.list_history.assert_called_once_with("run_1", top_n=3, sort_by="created")
