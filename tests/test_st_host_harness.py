from __future__ import annotations

from pathlib import Path

import pytest

from tests.st.hosts import (
    HOST_BY_MARKER,
    ST_ACTIVE_ENV,
    link_host_assets,
    st_host_from_marker_names,
)
from tests.st.helpers.claude_runner import ClaudeRunner
from tests.st.helpers.codex_runner import CodexRunner


def test_st_host_markers_select_one_agent() -> None:
    assert HOST_BY_MARKER["st_opencode"].kind == "opencode"
    assert HOST_BY_MARKER["st_codex"].kind == "codex"
    assert HOST_BY_MARKER["st_claude"].kind == "claude-code"
    assert HOST_BY_MARKER["st_pi_rpc"].kind == "pi-rpc"

    assert st_host_from_marker_names(["st", "st_codex"]) == "codex"
    assert st_host_from_marker_names(["st", "st_opencode"]) == "opencode"
    assert st_host_from_marker_names(["st", "st_pi_rpc"]) == "pi-rpc"
    assert st_host_from_marker_names(["st"]) == "opencode"

    with pytest.raises(ValueError, match="multiple ST host markers"):
        st_host_from_marker_names(["st", "st_codex", "st_claude"])


@pytest.mark.codex
def test_codex_runner_uses_exec_with_terra_model(tmp_path: Path) -> None:
    runner = CodexRunner(project_root=tmp_path / "project", log_dir=tmp_path)

    cmd = runner._build_cmd("run the prompt")

    assert cmd[:2] == ["codex", "exec"]
    assert ["-m", "gpt-5.6-terra"] == [
        cmd[cmd.index("-m")],
        cmd[cmd.index("-m") + 1],
    ]
    assert "-c" not in cmd
    assert ["-C", str(tmp_path / "project")] == [
        cmd[cmd.index("-C")],
        cmd[cmd.index("-C") + 1],
    ]
    assert "--skip-git-repo-check" in cmd
    assert "--dangerously-bypass-hook-trust" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "do not run pytest" in cmd[-1]
    assert "Codex /goal-plus system test" in cmd[-1]
    assert "run the prompt" in cmd[-1]


def test_claude_runner_uses_print_mode_with_project_mcp(tmp_path: Path) -> None:
    runner = ClaudeRunner(project_root=tmp_path / "project", log_dir=tmp_path)

    cmd = runner._build_cmd("run the prompt")

    assert cmd[:2] == ["claude", "-p"]
    assert "--mcp-config" in cmd
    assert str(tmp_path / "project" / ".mcp.json") in cmd
    assert "--permission-mode" in cmd
    assert "bypassPermissions" in cmd
    assert "do not run pytest" in cmd[-1]
    assert "Claude Code /goal-plus system test" in cmd[-1]


def test_st_active_env_guard_name_is_stable() -> None:
    assert ST_ACTIVE_ENV == "GOAL_PLUS_ST_ACTIVE"


def test_link_host_assets_supports_all_agent_hosts(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    for name in ("opencode.json", ".codex", ".mcp.json", ".claude", ".pi"):
        path = source / name
        if name in {".codex", ".claude", ".pi"}:
            path.mkdir()
        else:
            path.write_text("{}", encoding="utf-8")
    project = tmp_path / "run"
    project.mkdir()
    (project / ".agents").symlink_to(source / ".agents", target_is_directory=True)

    link_host_assets(project, source)

    for name in ("opencode.json", ".codex", ".mcp.json", ".claude", ".pi"):
        assert (project / name).exists()
    assert not (project / ".agents").is_symlink()
    assert not (project / ".agents").exists()


@pytest.mark.codex
def test_codex_redispatch_prompt_names_required_runtime_evidence() -> None:
    prompt = Path("tests/st/prompts/codex_redispatch.md").read_text(encoding="utf-8")

    assert "gpt-5.6-terra" in prompt
    assert 'worker_host="codex"' in prompt
    assert "search_redispatch_candidate" in prompt
    assert "two different agent_session_id" in prompt
    assert "parent_watchdog" in prompt


@pytest.mark.codex
def test_codex_circle_packing_cycle_is_strict_two_by_two_scenario() -> None:
    from tests.st.test_st_scenarios import SCENARIO_CASES

    case = next(
        case for case in SCENARIO_CASES if case.id == "codex_circle_packing_cycle"
    )
    assert {mark.name for mark in case.marks} == {"st", "st_codex"}

    prompt = Path("tests/st/prompts/codex_circle_packing_cycle.md").read_text(
        encoding="utf-8"
    )
    for required in (
        'worker_host="codex"',
        'worker_agent_type="search_candidate_agent"',
        "inherits the parent Codex model",
        "only `task_name`, `message`, and `fork_turns`",
        '"max_candidates": 4',
        '"max_parallel": 2',
        "batch_sizes: [2, 2]",
        "rounds: 2",
    ):
        assert required in prompt


@pytest.mark.codex
def test_codex_circle_packing_cycle_assertion_requires_complete_cycle() -> None:
    from tests.st import test_st_scenarios
    from tests.st.helpers.report_parser import StReport

    assert hasattr(test_st_scenarios, "_assert_codex_circle_packing_cycle")
    assertion = test_st_scenarios._assert_codex_circle_packing_cycle
    candidates = [
        {
            "candidate_id": f"c{index:03d}",
            "score": float(index),
            "iterations": 1,
            "status": "evaluated",
        }
        for index in range(1, 5)
    ]
    report = StReport(
        scenario="codex_circle_packing_cycle",
        run_id="run_cycle",
        candidates=candidates,
        selected_candidate_id="c004",
        best_score=4.0,
        report_path="/tmp/report.md",
        extra={
            "host": "codex",
            "model": "gpt-5.6-terra",
            "rounds": 2,
            "batch_sizes": [2, 2],
            "agent_session_ids": ["agent_1", "agent_2", "agent_3", "agent_4"],
        },
        raw="{}",
    )

    assertion(report)

    incomplete = StReport(**{**report.__dict__, "candidates": candidates[:3]})
    with pytest.raises(AssertionError):
        assertion(incomplete)

    duplicate_sessions = StReport(
        **{
            **report.__dict__,
            "extra": {
                **report.extra,
                "agent_session_ids": ["agent_1", "agent_1", "agent_3", "agent_4"],
            },
        }
    )
    with pytest.raises(AssertionError):
        assertion(duplicate_sessions)

    wrong_batches = StReport(
        **{**report.__dict__, "extra": {**report.extra, "batch_sizes": [2, 1]}}
    )
    with pytest.raises(AssertionError):
        assertion(wrong_batches)


def test_st_scenario_cases_cover_all_agent_hosts() -> None:
    from tests.st.test_st_scenarios import SCENARIO_CASES

    marker_names = {
        mark.name
        for case in SCENARIO_CASES
        for mark in case.marks
    }

    assert {"st_opencode", "st_codex", "st_claude"} <= marker_names


@pytest.mark.pi
def test_pi_rpc_circle_packing_cycle_is_selectable_by_marker() -> None:
    from tests.st import test_st_pi_rpc

    test_func = test_st_pi_rpc.test_pi_rpc_circle_packing_two_batch
    marker_names = {mark.name for mark in getattr(test_func, "pytestmark", [])}

    assert {"st", "st_pi_rpc"} <= marker_names
