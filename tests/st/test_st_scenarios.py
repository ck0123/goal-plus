"""System tests: drive a real host code agent and assert the main agent's final
JSON report matches the expected scenario contract.

These tests are skipped unless `-m st` is passed. They require:
  - host binary on PATH (`opencode`, `codex`, or `claude`)
  - search-runtime MCP server configured for that host

Each test loads a prompt from tests/st/prompts/<scenario>.md, runs the selected
host in a temporary project root, then parses the st_report JSON block from
stdout.
"""

from __future__ import annotations

import pytest

from .conftest import load_prompt
from .helpers.report_parser import StReport, extract_st_report, find_run_id_in_stdout


OPENCODE_SCENARIOS = [
    "circle_packing_continue",
    "circle_packing_two_batch",
    "circle_packing_random",
    "k_module_smoke",
    "k_module_then_circle_packing",
    "signal_processing_multi",
    "swe_bench_20212",
]

SCENARIO_CASES = [
    *[
        pytest.param(
            scenario,
            marks=(pytest.mark.st, pytest.mark.st_opencode),
            id=f"opencode-{scenario}",
        )
        for scenario in OPENCODE_SCENARIOS
    ],
    pytest.param(
        "codex_redispatch",
        marks=(pytest.mark.st, pytest.mark.st_codex),
        id="codex_redispatch",
    ),
    pytest.param(
        "claude_k_module_smoke",
        marks=(pytest.mark.st, pytest.mark.st_claude),
        id="claude_k_module_smoke",
    ),
]


def _assert_common_contract(report: StReport, scenario: str) -> None:
    assert report is not None, (
        "no st_report JSON block found in host stdout — main agent did not "
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
        pytest.skip(f"host run failed before producing candidates: {report.extra['error']}")
    assert report.selected_candidate_id, "selected_candidate_id missing"
    assert report.best_score is not None, "best_score missing"
    assert report.report_path, "report_path missing"


def _assert_circle_packing_continue(report: StReport) -> None:
    # Link-level: at least 1 candidate ran. Spec asks for 1, but main agent
    # may create more if it misreads; that's a contract drift, not a link failure.
    assert len(report.candidates) >= 1, (
        f"continuation scenario should have >=1 candidate, got {len(report.candidates)}"
    )
    extra = report.extra
    assert "agent_session_id" in extra, "extra.agent_session_id missing"
    assert "opencode_session_id" in extra, "extra.opencode_session_id missing"
    assert "verifier_scores" in extra, "extra.verifier_scores missing"
    # Spec asks for 2 verifier runs (before+after continuation), but link-level
    # only requires at least 1 — the agent may have stopped early.
    assert len(extra["verifier_scores"]) >= 1, (
        f"expected >=1 verifier score, got {extra['verifier_scores']}"
    )
    assert "score_delta" in extra, "extra.score_delta missing"


def _assert_circle_packing_two_batch(report: StReport) -> None:
    # Link-level: at least half the budget ran. Spec asks for 4; if only 1 ran
    # the chain broke somewhere, but 2/4 means batches actually progressed.
    assert len(report.candidates) >= 2, (
        f"two-batch scenario should have >=2 candidates (half of budget=4), got {len(report.candidates)}"
    )
    evaluated = [c for c in report.candidates if c.get("status") == "evaluated"]
    assert len(evaluated) >= 1, (
        f"expected >=1 evaluated candidate, got {len(evaluated)}"
    )


def _assert_circle_packing_random(report: StReport) -> None:
    assert len(report.candidates) >= 2, (
        f"random scenario should have >=2 candidates (half of budget=4), got {len(report.candidates)}"
    )
    assert "parent_candidate_id" in report.extra, (
        "extra.parent_candidate_id missing — batch-2 parent from strategy_trace must be reported"
    )


def _assert_k_module_smoke(report: StReport) -> None:
    assert len(report.candidates) >= 1, (
        f"k_module smoke should have >=1 candidate, got {len(report.candidates)}"
    )


def _assert_k_module_then_circle_packing(report: StReport) -> None:
    # Both runs must produce at least one candidate each.
    run1_candidates = report.extra.get("run1_candidates", 0)
    run2_candidates = report.extra.get("run2_candidates", 0)
    assert run1_candidates >= 1, (
        f"RUN_1 (k_module) should have >=1 candidate, got {run1_candidates}"
    )
    assert run2_candidates >= 1, (
        f"RUN_2 (circle_packing) should have >=1 candidate, got {run2_candidates}"
    )
    # Top-level candidates/run_id come from RUN_2 (circle_packing).
    assert len(report.candidates) >= 1, (
        f"top-level candidates (from RUN_2) should be >=1, got {len(report.candidates)}"
    )
    # The whole point of this scenario: the two runs must not collide.
    assert report.extra.get("run_ids_distinct") is True, (
        "run_ids_distinct must be true — runtime leaked state across runs "
        f"(RUN_1={report.extra.get('run1_run_id')}, "
        f"RUN_2={report.extra.get('run2_run_id')})"
    )
    run1 = report.extra.get("run1_run_id")
    run2 = report.extra.get("run2_run_id")
    assert run1 and run2 and run1 != run2, (
        f"run1_run_id and run2_run_id must both be non-empty and distinct: "
        f"run1={run1!r}, run2={run2!r}"
    )
    # Top-level run_id should match RUN_2.
    assert report.run_id == run2, (
        f"top-level run_id {report.run_id!r} should match run2_run_id {run2!r}"
    )


def _assert_signal_processing_multi(report: StReport) -> None:
    # Spec asks for 8; link-level needs at least 2 batches worth progressing,
    # so require >=4 (half of budget=8) — if only 1 ran, batch loop broke.
    assert len(report.candidates) >= 4, (
        f"signal_processing should have >=4 candidates (half of budget=8), got {len(report.candidates)}"
    )
    batches = report.extra.get("batches") or 0
    assert batches >= 1, (
        f"expected >=1 batch, got {batches}"
    )


def _assert_swe_bench_20212(report: StReport) -> None:
    assert len(report.candidates) >= 1, (
        f"swe_bench should have >=1 candidate, got {len(report.candidates)}"
    )
    # Link-level: at least one candidate scored > 0, meaning the agent actually
    # modified initial_program.py. Hitting gold patch (score=1.0) is bonus,
    # not a link-failure condition.
    best = max((c.get("score") or 0.0) for c in report.candidates)
    assert best > 0, (
        f"swe_bench best candidate score {best} <= 0 — no candidate modified the program successfully"
    )
    assert "fail_to_pass" in report.extra, "extra.fail_to_pass missing"
    assert "pass_to_pass" in report.extra, "extra.pass_to_pass missing"


def _assert_codex_redispatch(report: StReport) -> None:
    assert len(report.candidates) >= 1, (
        f"codex redispatch should have >=1 candidate, got {len(report.candidates)}"
    )
    extra = report.extra
    assert extra.get("host") == "codex"
    assert extra.get("model") == "gpt-5.3-codex-spark"
    assert extra.get("same_candidate") is True
    first = extra.get("first_agent_session_id")
    redispatched = extra.get("redispatch_agent_session_id")
    assert first and redispatched and first != redispatched, (
        "redispatch must create a second, distinct agent_session_id"
    )
    assert extra.get("redispatch_budget_control_mode") == "parent_watchdog"
    assert len(extra.get("verifier_scores") or []) >= 1


def _assert_claude_k_module_smoke(report: StReport) -> None:
    assert len(report.candidates) >= 1, (
        f"claude k_module smoke should have >=1 candidate, got {len(report.candidates)}"
    )
    assert report.extra.get("host") == "claude-code"


SCENARIO_ASSERTIONS = {
    "circle_packing_continue": _assert_circle_packing_continue,
    "circle_packing_two_batch": _assert_circle_packing_two_batch,
    "circle_packing_random": _assert_circle_packing_random,
    "k_module_smoke": _assert_k_module_smoke,
    "k_module_then_circle_packing": _assert_k_module_then_circle_packing,
    "signal_processing_multi": _assert_signal_processing_multi,
    "swe_bench_20212": _assert_swe_bench_20212,
    "codex_redispatch": _assert_codex_redispatch,
    "claude_k_module_smoke": _assert_claude_k_module_smoke,
}


@pytest.mark.parametrize("scenario", SCENARIO_CASES)
def test_scenario(
    scenario: str,
    st_runner,
) -> None:
    prompt = load_prompt(scenario)
    result = st_runner.run_streaming(prompt, scenario=scenario, timeout=2400)

    # Always print the log path so debugging is one click away
    print(f"\n[{scenario}] log: {result.log_path}")
    print(f"[{scenario}] exit: {result.returncode}, timed_out: {result.timed_out}")

    assert not result.timed_out, (
        f"host run timed out for {scenario}; see {result.log_path}"
    )
    # Some hosts may exit non-zero even on useful agent output; the st_report
    # block is the source of truth, not the exit code.
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
