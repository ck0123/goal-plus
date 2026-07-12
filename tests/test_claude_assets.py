from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_claude_mcp_json_registers_search_runtime() -> None:
    data = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))

    server = data["mcpServers"]["search-runtime"]
    assert server["command"] == "agentic-any-search-mcp"
    assert server["args"] == ["--root", ".gp"]


def test_claude_assets_wire_goal_plus_host_hooks() -> None:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    stop_hooks = settings["hooks"]["Stop"]
    post_tool_use_hooks = settings["hooks"]["PostToolUse"]

    assert not (ROOT / ".claude" / "settings.local.json").exists()
    assert settings["hooks"].keys() == {"Stop", "PostToolUse"}
    assert stop_hooks[0]["matcher"] == ""
    assert stop_hooks[0]["hooks"][0]["type"] == "command"
    command = stop_hooks[0]["hooks"][0]["command"]
    assert command == "agentic-any-search-mcp --goal-plus-host-hook"
    assert "python3" not in command
    assert post_tool_use_hooks[0]["matcher"] == ""
    post_command = post_tool_use_hooks[0]["hooks"][0]["command"]
    assert post_command == "agentic-any-search-mcp --goal-plus-host-hook"

    text = (ROOT / "docs" / "claude-code.md").read_text(encoding="utf-8")
    assert "ships Claude Code Goal Plus host hooks" in text
    assert "PostToolUse(goal_plus_create)" in text
    assert "does not wire PreToolUse or SubagentStop hooks" in text
    assert "agentic-any-search-mcp --goal-plus-host-hook" in text


def test_claude_skill_uses_foreground_agent_and_generic_bind() -> None:
    text = (ROOT / ".claude" / "skills" / "search" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "search_start_agent_session" in text
    assert "search_redispatch_candidate" in text
    assert "Agent" in text
    assert "search_bind_agent_handle" in text
    assert "SendMessage" in text
    assert "background: false" in text
    assert "background subagent" not in text.lower()


def test_claude_goal_plus_skill_records_modes_and_mcp_tools() -> None:
    text = (ROOT / ".claude" / "skills" / "goal-plus" / "SKILL.md").read_text(
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


def test_claude_worker_agent_calls_context_and_verifier() -> None:
    text = (ROOT / ".claude" / "agents" / "any-search-agent.md").read_text(
        encoding="utf-8"
    )

    assert "name: any-search-agent" in text
    assert "maxTurns: 8" in text
    assert "mcp__search-runtime__*" in text
    assert "search_get_agent_context" in text
    assert "search_run_verifier" in text


def test_claude_worker_agent_turn_budget_variants_exist() -> None:
    flash = (ROOT / ".claude" / "agents" / "any-search-agent-flash.md").read_text(
        encoding="utf-8"
    )
    deep = (ROOT / ".claude" / "agents" / "any-search-agent-deep.md").read_text(
        encoding="utf-8"
    )

    assert "name: any-search-agent-flash" in flash
    assert "maxTurns: 4" in flash
    assert "name: any-search-agent-deep" in deep
    assert "maxTurns: 16" in deep


def test_claude_search_skill_documents_tier_escalation_and_resume() -> None:
    text = (ROOT / ".claude" / "skills" / "search" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    agent = (ROOT / ".claude" / "agents" / "any-search-agent.md").read_text(
        encoding="utf-8"
    )

    assert "any-search-agent-flash" in text
    assert "any-search-agent-deep" in text
    assert "reached `maxTurns` before recording any verifier iteration" in text
    assert "History is runtime-owned, not a `plan.md` file" in text
    assert "state-level resume" in text
    assert "context.history" in text
    assert "context.iterations" in text
    assert "search_redispatch_candidate" in text
    assert "SendMessage` is unavailable" in text
    assert "do not rely on chat transcript" in agent


def test_claude_docs_record_log_inspection_paths() -> None:
    text = (ROOT / "docs" / "claude-code.md").read_text(encoding="utf-8")
    debug = (ROOT / "docs" / "debugging-runtime.md").read_text(encoding="utf-8")

    combined = text + "\n" + debug
    assert "--output-format stream-json" in combined
    assert "--debug-file" in combined
    assert "claude project purge" in combined
    assert "~/.claude/projects" in combined
    assert "subagents/" in combined


def test_claude_goal_plus_skill_documents_multiple_search_tasks() -> None:
    text = (ROOT / ".claude" / "skills" / "goal-plus" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "same `goal_plus_id`" in text
    assert "`search_tasks` is append-only" in text
    assert "`linked_search` is the current" in text
