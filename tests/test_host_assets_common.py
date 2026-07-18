"""Shared host-asset assertions consolidated from per-host test files.

Codex and Claude Code share the bulk of their ``goal-plus`` SKILL.md content
(same modes, same MCP tool names, same lifecycle language). Pi and OpenCode
diverge significantly — different MCP tool surfaces, different concepts — so
their dedicated tests remain in ``tests/test_<host>_assets.py``.

The two helpers below also let each host's budget-planning test delegate the
cross-host shared claims and keep only its unique extras inline.
"""

from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def assert_common_budget_planning_claims(text: str) -> None:
    """Budget-planning claims shared by every host's search SKILL.md.

    Each host's test should call this after reading its own SKILL.md, then
    add host-specific budget-planning assertions.
    """
    normalized = " ".join(text.split())
    assert "Search Run Budget Planning" in text
    assert "recommend 4" in text
    assert (
        "Different candidate ids do not by themselves provide search diversity"
        in normalized
    )
    assert "theoretical or structural limits" in normalized


def assert_common_goal_plus_skill_text(text: str) -> None:
    """Goal Plus SKILL.md claims shared by codex and claude.

    Pi and opencode have meaningfully different SKILL.md content (different
    MCP tool names, different concepts); they should NOT call this helper.
    """
    assert "name: goal-plus" in text
    assert "goal_plus_create" in text
    assert "goal_plus_record_triage" in text
    assert "goal_plus_save_spec_draft" in text
    assert "goal_plus_confirm_frozen_verifier" in text
    assert "goal_plus_gate" in text
    assert "mode_hint" not in text
    assert "Goal Mode" in text
    assert "Spec Discovery Mode" in text
    assert "Search Mode" in text
    assert '"recommended_phase": "goal"' in text
    assert "goal_mode" in text
    assert "Do not send fields named `mode` or `reason`" in text
    assert "Search is an autonomous upgrade" in text
    assert "without asking the user" in text
    assert "optional audit evidence" in text
    assert "Never pause or ask the user" in text
    assert "Do not create a SearchSpec in Goal Mode" in text
    assert "search_freeze_spec" in text
    assert "final raw-goal audit" in text
    assert "mode=autonomous" in text
    assert "mode=probe" in text
    assert ".goal-plus-verifiers/" in text
    assert "`expected_outputs`" in text


@pytest.mark.parametrize("host_dir", ["codex", "claude"])
def test_host_goal_plus_skill_records_modes_and_mcp_tools(host_dir: str) -> None:
    skill_path = ROOT / f".{host_dir}" / "skills" / "goal-plus" / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")

    assert_common_goal_plus_skill_text(text)

    if host_dir == "codex":
        assert "/goal-plus-with-final-check" in text
        assert "/goal-plus edit" in text
        assert "/goal-plus mode=autonomous" in text
        assert "/goal-plus mode=probe" in text
        assert "canonical final line in `raw_goal`" in text
        assert "A candidate lease ending never completes" in text
        assert "stores no separate task deadline" in text
        assert "treat the latest user message as" in text
        assert "scope, deliverables, or success criteria" in text
        assert "goal_plus_update_goal" in text
        assert "clarify before revising or resuming" in text
        assert "merely because the Goal Plus record is active" in text
        assert "goal_plus_prepare_final_check" in text
        assert "goal_plus_submit_final_check" in text
        assert "spawn_agent" in text
        assert 'fork_turns="none"' in text
        assert "never submit" in text
    else:  # claude
        normalized = " ".join(text.split())
        assert "canonical guidance" in text
        assert "worker lease ending is not goal completion" in normalized
