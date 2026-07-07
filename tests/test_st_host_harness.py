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


def test_codex_runner_uses_exec_with_spark_model(tmp_path: Path) -> None:
    runner = CodexRunner(project_root=tmp_path / "project", log_dir=tmp_path)

    cmd = runner._build_cmd("run the prompt")

    assert cmd[:2] == ["codex", "exec"]
    assert ["-m", "gpt-5.3-codex-spark"] == [
        cmd[cmd.index("-m")],
        cmd[cmd.index("-m") + 1],
    ]
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
    assert ST_ACTIVE_ENV == "AGENTIC_ANY_SEARCH_ST_ACTIVE"


def test_link_host_assets_supports_all_three_agents(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    for name in ("opencode.json", ".codex", ".agents", ".mcp.json", ".claude", ".pi"):
        path = source / name
        if name in {".codex", ".agents", ".claude", ".pi"}:
            path.mkdir()
        else:
            path.write_text("{}", encoding="utf-8")
    project = tmp_path / "run"
    project.mkdir()

    link_host_assets(project, source)

    for name in ("opencode.json", ".codex", ".agents", ".mcp.json", ".claude", ".pi"):
        assert (project / name).exists()


def test_codex_redispatch_prompt_names_required_runtime_evidence() -> None:
    prompt = Path("tests/st/prompts/codex_redispatch.md").read_text(encoding="utf-8")

    assert "gpt-5.3-codex-spark" in prompt
    assert 'worker_host="codex"' in prompt
    assert "search_redispatch_candidate" in prompt
    assert "two different agent_session_id" in prompt
    assert "parent_watchdog" in prompt


def test_st_scenario_cases_cover_all_agent_hosts() -> None:
    from tests.st.test_st_scenarios import SCENARIO_CASES

    marker_names = {
        mark.name
        for case in SCENARIO_CASES
        for mark in case.marks
    }

    assert {"st_opencode", "st_codex", "st_claude"} <= marker_names
