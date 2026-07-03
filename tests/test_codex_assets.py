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


def test_codex_worker_agent_calls_context_and_verifier() -> None:
    text = (ROOT / ".codex" / "agents" / "any_search_agent.toml").read_text(
        encoding="utf-8"
    )

    assert 'name = "any_search_agent"' in text
    assert "search_get_agent_context" in text
    assert "search_run_verifier" in text

