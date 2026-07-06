from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_codex_mcp_config_registers_search_runtime() -> None:
    text = (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")

    assert "[mcp_servers.search-runtime]" in text
    assert 'command = "agentic-any-search-mcp"' in text
    assert 'args = ["--root", ".search"]' in text


def test_codex_search_skill_uses_spawn_agent_and_generic_bind() -> None:
    text = (ROOT / ".agents" / "skills" / "search" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "search_start_agent_session" in text
    assert "spawn_agent" in text
    assert "search_bind_agent_handle" in text
    assert "search_bind_opencode_session" not in text
    assert "background" not in text.lower()


def test_codex_goal_plus_skill_records_modes_and_mcp_tools() -> None:
    text = (ROOT / ".agents" / "skills" / "goal-plus" / "SKILL.md").read_text(
        encoding="utf-8"
    )

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
    assert "Initial Search-Ready" in text
    assert "In-Progress Search Discovery" in text
    assert "Do not create a SearchSpec in Goal Mode" in text
    assert "search_freeze_spec" in text
    assert "final raw-goal audit" in text


def test_codex_search_skill_documents_worker_budget_watchdog() -> None:
    text = (ROOT / ".agents" / "skills" / "search" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "budget_control" in text
    assert "parent_watchdog" in text
    assert "wait_agent" in text
    assert "interrupt_agent" in text
    assert "send_input" in text
    assert "interrupt=true" in text


def test_codex_worker_agent_calls_context_and_verifier() -> None:
    text = (ROOT / ".codex" / "agents" / "any_search_agent.toml").read_text(
        encoding="utf-8"
    )

    assert 'name = "any_search_agent"' in text
    assert "search_get_agent_context" in text
    assert "search_run_verifier" in text


def test_codex_docs_record_log_inspection_paths() -> None:
    text = (ROOT / "docs" / "codex.md").read_text(encoding="utf-8")
    debug = (ROOT / "docs" / "debugging-runtime.md").read_text(encoding="utf-8")

    combined = text + "\n" + debug
    assert "codex exec --json" in combined
    assert "CODEX_HOME" in combined
    assert "rollout-*.jsonl" in combined
    assert "RUST_LOG=debug" in combined
    assert "log_dir=./.codex-log" in combined
