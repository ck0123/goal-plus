from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_claude_mcp_json_registers_search_runtime() -> None:
    data = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))

    server = data["mcpServers"]["search-runtime"]
    assert server["command"] == "agentic-any-search-mcp"
    assert server["args"] == ["--root", ".search"]


def test_claude_skill_uses_foreground_agent_and_generic_bind() -> None:
    text = (ROOT / ".claude" / "skills" / "search" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "search_start_agent_session" in text
    assert "Agent" in text
    assert "search_bind_agent_handle" in text
    assert "SendMessage" in text
    assert "background: false" in text
    assert "background subagent" not in text.lower()


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


def test_claude_docs_record_log_inspection_paths() -> None:
    text = (ROOT / "docs" / "claude-code.md").read_text(encoding="utf-8")
    debug = (ROOT / "docs" / "debugging-runtime.md").read_text(encoding="utf-8")

    combined = text + "\n" + debug
    assert "--output-format stream-json" in combined
    assert "--debug-file" in combined
    assert "claude project purge" in combined
    assert "~/.claude/projects" in combined
    assert "subagents/" in combined
