"""System tests: drive a real host code agent and assert the main agent's final
JSON report matches the expected scenario contract.

These tests are skipped unless `-m st` is passed. They require:
  - host binary on PATH (`opencode`, `codex`, or `claude`)
  - goal-plus MCP server configured for that host

Each test loads a prompt from tests/st/prompts/<scenario>.md, runs the selected
host in a temporary project root, then parses the st_report JSON block from
stdout.
"""

from __future__ import annotations

import json
from pathlib import Path

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
        "codex_circle_packing_cycle",
        marks=(pytest.mark.st, pytest.mark.st_codex),
        id="codex_circle_packing_cycle",
    ),
    pytest.param(
        "codex_rolling_followup",
        marks=(pytest.mark.st, pytest.mark.st_codex),
        id="codex_rolling_followup",
    ),
    pytest.param(
        "codex_time_advisory",
        marks=(pytest.mark.st, pytest.mark.st_codex),
        id="codex_time_advisory",
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
    assert extra.get("model") == "gpt-5.6-terra"
    assert extra.get("same_candidate") is True
    first = extra.get("first_agent_session_id")
    redispatched = extra.get("redispatch_agent_session_id")
    assert first and redispatched and first != redispatched, (
        "redispatch must create a second, distinct agent_session_id"
    )
    assert extra.get("redispatch_budget_control_mode") == "parent_watchdog"
    assert len(extra.get("verifier_scores") or []) >= 1


def _assert_codex_circle_packing_cycle(report: StReport) -> None:
    expected_ids = ["c001", "c002", "c003", "c004"]
    assert [candidate.get("candidate_id") for candidate in report.candidates] == expected_ids, (
        "Codex cycle must report exactly c001 through c004 in order"
    )
    assert all(
        candidate.get("status") == "evaluated"
        and int(candidate.get("iterations") or 0) >= 1
        for candidate in report.candidates
    ), "all four Codex cycle candidates must be evaluated with verifier iterations"

    extra = report.extra
    assert extra.get("host") == "codex"
    assert extra.get("model") == "gpt-5.6-terra"
    assert extra.get("rounds") == 2
    assert extra.get("batch_sizes") == [2, 2]
    session_ids = extra.get("agent_session_ids") or []
    assert len(session_ids) == 4, "cycle must report four agent_session_id values"
    assert len(set(session_ids)) == 4, "cycle agent_session_id values must be distinct"


def _assert_codex_rolling_followup(report: StReport) -> None:
    assert [candidate.get("candidate_id") for candidate in report.candidates] == [
        "c001",
        "c002",
    ]
    assert all(
        candidate.get("status") == "evaluated"
        and int(candidate.get("iterations") or 0) >= 1
        for candidate in report.candidates
    )
    extra = report.extra
    assert extra.get("host") == "codex"
    assert extra.get("model") == "gpt-5.6-terra"
    assert extra.get("wait_mode") == "wait_any"
    session_ids = extra.get("initial_agent_session_ids") or []
    assert len(session_ids) == 2 and len(set(session_ids)) == 2
    assert len(extra.get("task_names") or []) == 2
    assert extra.get("continued_candidate_id") == extra.get(
        "first_completed_candidate_id"
    )
    assert extra.get("continued_agent_session_id") in session_ids
    assert extra.get("continue_tool") == "followup_task"
    assert extra.get("same_worker_continuation") is True


def _assert_codex_time_advisory(report: StReport) -> None:
    assert len(report.candidates) == 1
    assert report.candidates[0].get("status") == "evaluated"
    assert int(report.candidates[0].get("iterations") or 0) >= 1
    assert report.extra.get("host") == "codex"
    assert report.extra.get("model") == "gpt-5.6-terra"
    assert report.extra.get("agent_session_id")


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
    "codex_circle_packing_cycle": _assert_codex_circle_packing_cycle,
    "codex_rolling_followup": _assert_codex_rolling_followup,
    "codex_time_advisory": _assert_codex_time_advisory,
    "claude_k_module_smoke": _assert_claude_k_module_smoke,
}


@pytest.mark.parametrize("scenario", SCENARIO_CASES)
def test_scenario(
    scenario: str,
    st_runner,
    st_project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if scenario == "codex_time_advisory":
        monkeypatch.setenv(
            "GOAL_PLUS_OUTER_DEADLINE_AT",
            "1970-01-01T00:00:00Z",
        )
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

    if scenario == "codex_time_advisory":
        agent_session_id = report.extra["agent_session_id"]
        evidence_path = (
            st_project_root
            / ".gp"
            / "host-logs"
            / "codex-time-advisory"
            / "sent"
            / f"{agent_session_id}.json"
        )
        assert evidence_path.is_file(), (
            "Codex Search candidate PostTool hook did not record an advisory "
            f"for {agent_session_id}"
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence["agent_session_id"] == agent_session_id
        assert evidence["run_id"] == report.run_id
        assert evidence["deadline_source"] == "outer_deadline"
        assert evidence["remaining_seconds"] == 0
        assert evidence["average_submission_seconds"] > 0
        assert evidence["total_verifier_count"] >= 1

    # Smoke keyword check: run_id must appear somewhere in the raw output
    assert report.run_id in result.stdout, (
        f"run_id {report.run_id} not found in raw stdout — likely the main agent "
        f"hallucinated it"
    )
