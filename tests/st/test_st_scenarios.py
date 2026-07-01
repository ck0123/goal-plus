"""System tests: drive `opencode run --command search` and assert the main
agent's final JSON report matches the expected scenario contract.

These tests are skipped unless `-m st` is passed. They require:
  - opencode binary on PATH
  - search-runtime MCP server connected (verified via `opencode mcp list`)

Each test loads a prompt from tests/st/prompts/<scenario>.md, runs opencode in
a temporary project root, then parses the st_report JSON block from stdout.
"""

from __future__ import annotations

import pytest

from .conftest import load_prompt
from .helpers.report_parser import StReport, extract_st_report, find_run_id_in_stdout


SCENARIOS = [
    "circle_packing_continue",
    "circle_packing_two_batch",
    "circle_packing_random",
    "k_module_smoke",
    "signal_processing_multi",
    "swe_bench_20212",
]


def _assert_common_contract(report: StReport, scenario: str) -> None:
    assert report is not None, (
        "no st_report JSON block found in opencode stdout — main agent did not "
        "emit the ST output contract; check the log file for the full session"
    )
    assert report.scenario == scenario, (
        f"scenario mismatch: expected {scenario}, got {report.scenario}"
    )
    assert report.run_id, "run_id missing in st_report"
    # candidates is allowed to be empty only if extra.error is set
    if not report.candidates:
        assert report.extra.get("error"), (
            "candidates empty but no error reason in extra.error"
        )
        pytest.skip(f"opencode run failed before producing candidates: {report.extra['error']}")
    assert report.selected_candidate_id, "selected_candidate_id missing"
    assert report.best_score is not None, "best_score missing"
    assert report.report_path, "report_path missing"


def _assert_circle_packing_continue(report: StReport) -> None:
    assert len(report.candidates) == 1, (
        f"continuation scenario should have exactly 1 candidate, got {len(report.candidates)}"
    )
    extra = report.extra
    assert "agent_session_id" in extra, "extra.agent_session_id missing"
    assert "opencode_session_id" in extra, "extra.opencode_session_id missing"
    assert "verifier_scores" in extra, "extra.verifier_scores missing"
    assert len(extra["verifier_scores"]) == 2, (
        f"expected 2 verifier scores (before+after continuation), got {extra['verifier_scores']}"
    )
    assert "score_delta" in extra, "extra.score_delta missing"


def _assert_circle_packing_two_batch(report: StReport) -> None:
    assert len(report.candidates) == 4, (
        f"two-batch scenario should have 4 candidates, got {len(report.candidates)}"
    )
    evaluated = [c for c in report.candidates if c.get("status") == "evaluated"]
    assert len(evaluated) == 4, f"expected 4 evaluated candidates, got {len(evaluated)}"


def _assert_circle_packing_random(report: StReport) -> None:
    assert len(report.candidates) == 4, (
        f"random scenario should have 4 candidates, got {len(report.candidates)}"
    )
    assert "parent_candidate_id" in report.extra, (
        "extra.parent_candidate_id missing — batch-2 parent from strategy_trace must be reported"
    )


def _assert_k_module_smoke(report: StReport) -> None:
    assert len(report.candidates) == 2, (
        f"k_module smoke should have 2 candidates, got {len(report.candidates)}"
    )


def _assert_signal_processing_multi(report: StReport) -> None:
    assert len(report.candidates) == 8, (
        f"signal_processing should have 8 candidates, got {len(report.candidates)}"
    )
    assert report.extra.get("batches") == 2, (
        f"expected 2 batches, got {report.extra.get('batches')}"
    )


def _assert_swe_bench_20212(report: StReport) -> None:
    assert len(report.candidates) == 4, (
        f"swe_bench should have 4 candidates, got {len(report.candidates)}"
    )
    # at least one candidate should achieve score 1.0 (gold patch)
    best = max((c.get("score") or 0.0) for c in report.candidates)
    assert best >= 1.0, (
        f"swe_bench best candidate score {best} < 1.0 — gold patch not reached"
    )
    assert "fail_to_pass" in report.extra, "extra.fail_to_pass missing"
    assert "pass_to_pass" in report.extra, "extra.pass_to_pass missing"


SCENARIO_ASSERTIONS = {
    "circle_packing_continue": _assert_circle_packing_continue,
    "circle_packing_two_batch": _assert_circle_packing_two_batch,
    "circle_packing_random": _assert_circle_packing_random,
    "k_module_smoke": _assert_k_module_smoke,
    "signal_processing_multi": _assert_signal_processing_multi,
    "swe_bench_20212": _assert_swe_bench_20212,
}


@pytest.mark.st
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario(
    scenario: str,
    opencode_runner,
) -> None:
    prompt = load_prompt(scenario)
    result = opencode_runner.run_streaming(prompt, scenario=scenario, timeout=2400)

    # Always print the log path so debugging is one click away
    print(f"\n[{scenario}] log: {result.log_path}")
    print(f"[{scenario}] exit: {result.returncode}, timed_out: {result.timed_out}")

    assert not result.timed_out, (
        f"opencode run timed out for {scenario}; see {result.log_path}"
    )
    # OpenCode may exit non-zero even on success; the st_report block is the
    # source of truth, not the exit code.
    report = extract_st_report(result.stdout)
    if report is None:
        run_id_fallback = find_run_id_in_stdout(result.stdout)
        pytest.fail(
            f"no st_report JSON block in stdout for {scenario} "
            f"(fallback run_id guess: {run_id_fallback}); full log: {result.log_path}"
        )

    _assert_common_contract(report, scenario)
    SCENARIO_ASSERTIONS[scenario](report)

    # Smoke keyword check: run_id must appear somewhere in the raw output
    assert report.run_id in result.stdout, (
        f"run_id {report.run_id} not found in raw stdout — likely the main agent "
        f"hallucinated it"
    )
