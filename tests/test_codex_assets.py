from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_codex_mcp_config_registers_search_runtime() -> None:
    text = (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")

    assert "[mcp_servers.search-runtime]" in text
    assert 'command = "agentic-any-search-mcp"' in text
    assert 'args = ["--root", ".search"]' in text


def test_codex_assets_wire_goal_plus_host_hooks() -> None:
    hooks = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    stop_hooks = hooks["hooks"]["Stop"]
    post_tool_use_hooks = hooks["hooks"]["PostToolUse"]

    assert hooks["hooks"].keys() == {"Stop", "PostToolUse"}
    assert stop_hooks[0]["hooks"][0]["type"] == "command"
    command = stop_hooks[0]["hooks"][0]["command"]
    assert command == "agentic-any-search-mcp --goal-plus-host-hook"
    assert "python3" not in command
    assert stop_hooks[0]["hooks"][0]["timeout"] == 30
    post_command = post_tool_use_hooks[0]["hooks"][0]["command"]
    assert post_command == "agentic-any-search-mcp --goal-plus-host-hook"
    assert post_tool_use_hooks[0]["hooks"][0]["timeout"] == 30

    text = (ROOT / "docs" / "codex.md").read_text(encoding="utf-8")
    assert "ships project-local Goal Plus host hooks" in text
    assert "PostToolUse(goal_plus_create)" in text
    assert "does not wire PreToolUse or SubagentStop hooks" in text
    assert "agentic-any-search-mcp --goal-plus-host-hook" in text


def test_codex_search_skill_uses_spawn_agent_and_generic_bind() -> None:
    text = (ROOT / ".agents" / "skills" / "search" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "search_start_agent_session" in text
    assert "search_redispatch_candidate" in text
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
    assert '"recommended_phase": "goal"' in text
    assert "goal_mode" in text
    assert "Do not send fields named `mode` or `reason`" in text
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


def test_codex_search_skill_documents_state_level_resume() -> None:
    text = (ROOT / ".agents" / "skills" / "search" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    agent = (ROOT / ".codex" / "agents" / "any_search_agent.toml").read_text(
        encoding="utf-8"
    )

    assert "History is runtime-owned, not a `plan.md` file" in text
    assert "State-level resume" in text
    assert "context.history" in text
    assert "context.iterations" in text
    assert "worker_budget.max_runtime_seconds" in text
    assert "search_redispatch_candidate" in text
    assert "do not rely on chat transcript" in agent


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
